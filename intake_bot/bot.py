"""Telegram bot: photo -> proposal -> confirm/correct -> sheet row.

Long-polling (Application.run_polling), so it needs no public URL — run it
as a service on any always-on machine.
"""

from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ParseMode
from telegram.error import TimedOut
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

from .caption import apply_sale_price, parse_caption
from .catalog import Catalog
from .config import Settings
from .item import DEFAULT_QUANTITY, ItemFields, auto_descriptor, review, sample_rows, to_import_row
from .naming import decode_cipher
from .sheet import Sheet
from .store import Intake, Store
from .vision import Vision, VisionError

log = logging.getLogger(__name__)
ALBUM_WAIT_SECONDS = 2.0

HELP = (
    "Send a photo of one item (add the tag/box as extra photos in the same album).\n"
    "Caption: brand, the price number, gender, SKU, and optionally strap/colors in tag order — e.g.\n"
    "<code>Seiko 1.250.000 sku SRPD53K1 G ss silver bd silver 1pcs</code>\n"
    "<code>AC 950k ref 1007 L leather black bd rose gold qty 2</code>\n"
    "• List brands (AC, EXP, Seiko, Casio…): type the distributor <b>list price</b>.\n"
    "• Nett brands (Bonia, Timberland…): type the <b>cost</b>.\n"
    "• SALE brands: type the selling price.\n"
    "Clocks: add <code>JD</code> or <code>clock</code>, e.g. <code>Seiko JD 250k sku QXA756W</code>.\n"
    "Then reply to my summary in plain words to fix anything (\"rubber not leather, ladies\"), "
    "and press ✅ when it's right."
)


class IntakeBot:
    def __init__(self, settings: Settings, catalog: Catalog, store: Store, vision: Vision, sheet: Sheet):
        self.s, self.catalog, self.store, self.vision, self.sheet = settings, catalog, store, vision, sheet
        self.photo_dir = settings.data_dir / "photos"
        self.photo_dir.mkdir(parents=True, exist_ok=True)
        self._albums: dict[str, list[Message]] = {}

    # ---------- access ----------

    async def _allowed(self, update: Update) -> bool:
        user = update.effective_user
        if user and user.id in self.s.allowed_user_ids:
            return True
        if update.effective_message:
            await update.effective_message.reply_text(
                f"This bot is private. Ask the owner to add your Telegram ID: {user.id if user else '?'}")
        log.warning("Rejected user %s", user.id if user else None)
        return False

    # ---------- handlers ----------

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._allowed(update):
            await update.effective_message.reply_text(HELP, parse_mode=ParseMode.HTML)

    async def photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._allowed(update):
            return
        msg = update.effective_message
        if msg.media_group_id:
            # Albums arrive as one update per photo; collect them, then process once.
            group = self._albums.setdefault(msg.media_group_id, [])
            group.append(msg)
            if len(group) == 1:
                context.job_queue.run_once(self._album_ready, ALBUM_WAIT_SECONDS,
                                           data=msg.media_group_id, name=f"album-{msg.media_group_id}")
            return
        await self._process([msg])

    async def _album_ready(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        messages = self._albums.pop(context.job.data, [])
        if messages:
            await self._process(messages)

    async def _process(self, messages: list[Message]) -> None:
        first = messages[0]
        user = first.from_user
        caption = next((m.caption for m in messages if m.caption), None)
        intake_id = self.store.new_id()
        status = await first.reply_text("Looking at the photo…")

        # Save the largest size of each photo durably (Telegram file URLs expire).
        images, paths = [], []
        for n, m in enumerate(messages, start=1):
            file = await m.photo[-1].get_file()
            data = bytes(await file.download_as_bytearray())
            path = self.photo_dir / f"{intake_id}_{n}.jpg"
            path.write_bytes(data)
            images.append((data, "image/jpeg"))
            paths.append(str(path))

        try:
            proposed = await asyncio.to_thread(self.vision.propose, images, caption)
        except VisionError as e:
            proposed = ItemFields(notes=f"Photo not read: {e}")

        fields = apply_sale_price(self.catalog, proposed.merged(parse_caption(self.catalog, caption)))
        intake = self.store.create(intake_id, first.chat_id, user.id, user.full_name, fields, paths)
        await status.delete()
        await self._send_summary(first, intake)

    async def text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._allowed(update):
            return
        msg = update.effective_message
        intake = None
        if msg.reply_to_message:
            intake = self.store.by_message(msg.chat_id, msg.reply_to_message.message_id)
        intake = intake or self.store.latest_pending(msg.chat_id, msg.from_user.id)
        if not intake or intake.status != "pending":
            await msg.reply_text("Nothing pending to correct. Send a photo to start a new item.")
            return
        try:
            corrected = await asyncio.to_thread(self.vision.correct, intake.fields, msg.text)
        except VisionError as e:
            await msg.reply_text(f"Couldn't apply that: {e}")
            return
        fields = apply_sale_price(self.catalog, corrected)
        self.store.update(intake.id, fields=fields)
        await self._send_summary(msg, self.store.get(intake.id))

    async def button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not await self._allowed(update):
            await query.answer()
            return
        action, intake_id = query.data.split(":", 1)
        intake = self.store.get(intake_id)
        if not intake or intake.status != "pending":
            await query.answer("Already handled.")
            return

        if action == "cancel":
            self.store.update(intake.id, status="cancelled")
            await query.answer("Cancelled.")
            await query.edit_message_reply_markup(None)
            await query.message.reply_text(f"❌ {intake.id} cancelled — nothing written.")
            return

        r = review(self.catalog, intake.fields)
        if not r.ok:
            await query.answer("Still has problems — see the list.", show_alert=True)
            return
        row = to_import_row(self.catalog, intake.fields)
        notes = intake.fields.notes or ""
        row.update({
            "intake_id": intake.id,
            "logged_by": f"{intake.user_name} ({intake.user_id})",
            "logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "photos": ", ".join(intake.photos),
            "preview_name": r.name or "",
            "preview_sku": r.sku or "",
            "notes": notes,
            "photo_dial_color": intake.fields.photo_dial_color or "",
            "photo_index_type": intake.fields.photo_index_type or "",
            "receipt_date": self.sheet.today().isoformat(),
        })
        qty = int(row.get("quantity") or 0)

        # Same SKU already received TODAY -> staff chose Replace or Add qty.
        # Look it up again now: the day may have changed since the summary.
        today_rows = await asyncio.to_thread(self.sheet.same_sku_today, r.sku or "")
        if action in ("replace", "addqty") and today_rows:
            existing = today_rows[0]
            if action == "replace":
                row["notes"] = f"{row['notes']} [replaces {existing.get('intake_id', '?')}]".strip()
                await asyncio.to_thread(self.sheet.replace, existing["_row"], row)
                text = f"🔁 {intake.id} replaced today's {existing.get('intake_id', '?')} for {r.sku} ({qty} pcs)."
            else:
                new_qty = await asyncio.to_thread(self.sheet.add_quantity, existing, qty, f"+{qty} ({intake.id})")
                text = f"➕ Added {qty} to today's {existing.get('intake_id', '?')} for {r.sku}: now {new_qty} pcs."
            self.store.update(intake.id, status="merged" if action == "addqty" else "confirmed")
        elif action == "confirm" and today_rows:
            await query.answer("Already received today: choose Replace or Add qty.", show_alert=True)
            return
        else:
            # New receipt line (a different date counts as a new receipt).
            await asyncio.to_thread(self.sheet.append, row)
            self.store.update(intake.id, status="confirmed")
            text = f"✅ {intake.id} received ({qty} pcs) on {row['receipt_date']}, added to {self.sheet.tab_title()}."
        await query.answer("Saved.")
        await query.edit_message_reply_markup(None)
        await query.message.reply_text(f"{text}\n{r.name or ''}")

    # ---------- summary ----------

    async def _apply_auto_descriptor(self, intake: Intake) -> tuple[Intake, str | None]:
        """If this item would print the same name as a row already in the
        sheet, add a Unique Descriptor from what Claude saw (dial, then index)."""
        if not review(self.catalog, intake.fields).ok or intake.fields.dial_color:
            return intake, None
        try:
            clashes = await asyncio.to_thread(self.sheet.similar, to_import_row(self.catalog, intake.fields))
        except Exception:
            log.exception("Duplicate-name check failed")
            return intake, None
        result = auto_descriptor(self.catalog, intake.fields, clashes)
        if not result:
            return intake, None
        fields, text = result
        self.store.update(intake.id, fields=fields)
        ids = ", ".join(str(c.get("intake_id", "?")) for c in clashes)
        return self.store.get(intake.id), f"🔹 Added descriptor <code>{html.escape(text)}</code>: {html.escape(ids)} would otherwise print the same name."

    async def _send_summary(self, reply_to: Message, intake: Intake) -> None:
        intake, descriptor_note = await self._apply_auto_descriptor(intake)
        f = intake.fields
        r = review(self.catalog, f)
        e = html.escape

        def line(label: str, value, key: str | None = None) -> str:
            mark = " ❓" if key and key in f.uncertain else ""
            return f"{label}: <b>{e(str(value))}</b>{mark}" if value not in (None, "") else f"{label}: —"

        lines = [f"<b>Item {e(intake.id)}</b>",
                 line("Type", f.type or (self.catalog.brands.get(f.brand).kind if self.catalog.brands.get(f.brand) else None), "type"),
                 line("Brand", f.brand, "brand")]
        if (f.type or "watch") == "watch":
            lines += [line("Strap", f.strap_type, "strap_type"), line("Strap color", f.strap_color, "strap_color")]
        lines += [line("Case color", f.case_color, "case_color")]
        if (f.type or "watch") == "watch":
            lines.append(line("Gender", f.gender))
        for label, key in (("Dial", "dial_color"), ("Index", "index_type"), ("Crown", "crown_color")):
            if getattr(f, key):
                lines.append(line(label, getattr(f, key), key))
        lines += [line("Ref code", f.ref_code), line("Mfr SKU", f.manufacturer_sku, "manufacturer_sku")]
        if f.supplied_price:
            lines.append(line("Supplied price", f"Rp{f.supplied_price:,.0f}"))
        if f.selling_price:
            lines.append(line("Selling price", f"Rp{f.selling_price:,.0f}"))
        lines.append(line("Qty", f.quantity) if f.quantity is not None else f"Qty: <b>{DEFAULT_QUANTITY}</b> (default)")
        seen = " · ".join(x for x in (f.photo_dial_color and f"dial {f.photo_dial_color}",
                                      f.photo_index_type and f"{f.photo_index_type} index") if x)
        if seen:
            lines.append(f"👁 Seen: {e(seen)}")
        if r.hjb:
            hbb = f"HBB Rp{r.hbb:,.0f} · " if r.hbb is not None else ""
            lines.append(f"💰 {hbb}HJB Rp{r.hjb:,.0f}")
        if r.name:
            lines.append(f"🏷 <code>{e(r.name)}</code>")
        if r.sku:
            lines.append(f"🔖 <code>{e(r.sku)}</code>")
        if f.notes:
            lines.append(f"📝 {e(f.notes)}")
            hint = self._tag_hint(f.notes, r.hjb)
            if hint:
                lines.append(hint)
        same_sku: list = []
        if r.ok:
            try:
                same_sku = await asyncio.to_thread(self.sheet.same_sku_today, r.sku or "")
                dupes = await asyncio.to_thread(self.sheet.similar, to_import_row(self.catalog, f))
            except Exception:  # sheet hiccups must not block the summary
                log.exception("Duplicate check failed")
                same_sku, dupes = [], []
            if same_sku:
                prev = same_sku[0]
                lines.append(f"🔁 SKU {e(r.sku)} was already received <b>today</b> ({e(str(prev.get('intake_id', '?')))}, "
                             f"{e(str(prev.get('quantity', '?')))} pcs). <b>Replace</b> if it's the same item logged "
                             f"twice; <b>Add qty</b> if it's another unit.")
            elif dupes:
                last = dupes[-1]
                lines.append(f"⚠️ Same name as {e(str(last.get('intake_id', '?')))} (received "
                             f"{e(str(last.get('receipt_date') or last.get('_tab', '?')))}). Same model → this is a "
                             "restock, fine. A different model → reply with a descriptor, e.g. \"dial blue\".")
        if descriptor_note:
            lines.append(descriptor_note)
        if f.uncertain:
            lines.append("❓ = not sure from the photo, please check.")
        if r.errors:
            lines.append("\n<b>Needed before saving:</b>")
            lines += [f"• {e(err)}" for err in r.errors]
        lines.append("\nReply to this message to correct anything.")

        buttons = [[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel:{intake.id}")]]
        if r.ok and same_sku:
            buttons[0][:0] = [InlineKeyboardButton("🔁 Replace", callback_data=f"replace:{intake.id}"),
                              InlineKeyboardButton("➕ Add qty", callback_data=f"addqty:{intake.id}")]
        elif r.ok:
            buttons[0].insert(0, InlineKeyboardButton("✅ Confirm", callback_data=f"confirm:{intake.id}"))
        sent = await reply_to.reply_text("\n".join(lines), parse_mode=ParseMode.HTML,
                                         reply_markup=InlineKeyboardMarkup(buttons))
        if intake.message_id:
            try:  # Only the newest summary keeps working buttons.
                await reply_to.get_bot().edit_message_reply_markup(intake.chat_id, intake.message_id, reply_markup=None)
            except Exception:
                pass
        self.store.update(intake.id, message_id=sent.message_id)

    def _tag_hint(self, notes: str, hjb: float | None) -> str | None:
        """If the photo showed a tag cipher, check it against the computed HJB."""
        marker = "tag cipher:"
        low = notes.lower()
        if marker not in low:
            return None
        letters = notes[low.index(marker) + len(marker):].strip().split()[0].strip(".,;")
        tag_hjb = decode_cipher(self.catalog, letters)
        if tag_hjb is None:
            return None
        if hjb is None:
            return f"🔎 Tag {letters} = HJB ≈ Rp{tag_hjb:,.0f}"
        if int(hjb) // 1000 * 1000 != tag_hjb:
            return f"⚠️ Tag {letters} says HJB ≈ Rp{tag_hjb:,.0f}, but the price you typed gives Rp{hjb:,.0f}."
        return f"✔️ Tag {letters} matches the computed HJB."


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and tell the sender, instead of silently dropping their photo."""
    log.error("Error while handling an update", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        reason = "Telegram was slow to respond" if isinstance(context.error, TimedOut) else "Something went wrong"
        try:
            await update.effective_message.reply_text(f"⚠️ {reason}. Nothing was saved — please send it again.")
        except Exception:
            log.exception("Couldn't send the error notice")


def build_application(settings: Settings) -> Application:
    catalog = Catalog.load()
    store = Store(settings.data_dir / "intake.sqlite3")
    vision = Vision(catalog, settings.claude_model)
    sheet = Sheet(settings.google_credentials_file, settings.spreadsheet_id, settings.worksheet, settings.timezone)
    bot = IntakeBot(settings, catalog, store, vision, sheet)
    try:
        sheet.write_guide(sample_rows(catalog))
    except Exception:  # the guide is a convenience; never block startup on it
        log.exception("Couldn't write the Legend tab / header notes")

    # Telegram's default 5 s read timeout is too short for file downloads on
    # slower routes; a lost photo is worse than a slow one.
    app = (Application.builder().token(settings.telegram_token)
           .connect_timeout(15).read_timeout(30).write_timeout(30).pool_timeout(15)
           .get_updates_connect_timeout(15).get_updates_read_timeout(30)
           .build())
    app.add_handler(CommandHandler(["start", "help"], bot.start))
    app.add_handler(MessageHandler(filters.PHOTO, bot.photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.text))
    app.add_handler(CallbackQueryHandler(bot.button))
    app.add_error_handler(on_error)
    return app
