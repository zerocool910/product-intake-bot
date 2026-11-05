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
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

from .caption import apply_sale_price, parse_caption
from .catalog import Catalog
from .config import Settings
from .item import ItemFields, review, to_import_row
from .naming import decode_cipher
from .sheet import Sheet
from .store import Intake, Store
from .vision import Vision, VisionError

log = logging.getLogger(__name__)
ALBUM_WAIT_SECONDS = 2.0

HELP = (
    "Send a photo of one item (add the tag/box as extra photos in the same album).\n"
    "Caption: brand, the price number, gender — e.g. <code>Seiko 1.250.000 G</code> "
    "or <code>AC 950k ref AE1200 L qty 2</code>.\n"
    "• List brands (AC, EXP, Seiko, Casio…): type the distributor <b>list price</b>.\n"
    "• Nett brands (Bonia, Timberland…): type the <b>cost</b>.\n"
    "• SALE brands: type the selling price.\n"
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
        row.update({
            "intake_id": intake.id,
            "logged_by": f"{intake.user_name} ({intake.user_id})",
            "logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "photos": ", ".join(intake.photos),
            "preview_name": r.name or "",
            "preview_sku": r.sku or "",
            "notes": intake.fields.notes or "",
        })
        await asyncio.to_thread(self.sheet.append, row)
        self.store.update(intake.id, status="confirmed")
        await query.answer("Saved.")
        await query.edit_message_reply_markup(None)
        await query.message.reply_text(f"✅ {intake.id} added to the sheet.\n{r.name or ''}")

    # ---------- summary ----------

    async def _send_summary(self, reply_to: Message, intake: Intake) -> None:
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
        if f.quantity:
            lines.append(line("Qty", f.quantity))
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
        if r.ok:
            try:
                same_sku = await asyncio.to_thread(self.sheet.same_sku, r.sku or "")
                dupes = await asyncio.to_thread(self.sheet.similar, to_import_row(self.catalog, f))
            except Exception:  # sheet hiccups must not block the summary
                log.exception("Duplicate check failed")
                same_sku, dupes = [], []
            if same_sku:
                ids = ", ".join(str(x.get("intake_id", "?")) for x in same_sku)
                lines.append(f"⛔ SKU {e(r.sku)} is already in the sheet ({e(ids)}). The import rejects "
                             "repeated SKUs. If this is more stock of the same item, cancel this and add "
                             "the qty to that row, or record it as a stock receipt in accounting.")
            elif dupes:
                lines.append(f"⚠️ {len(dupes)} item(s) in the sheet would print the same name. "
                             "If this is a different model, add a descriptor (e.g. \"dial blue\").")
        if f.uncertain:
            lines.append("❓ = not sure from the photo, please check.")
        if r.errors:
            lines.append("\n<b>Needed before saving:</b>")
            lines += [f"• {e(err)}" for err in r.errors]
        lines.append("\nReply to this message to correct anything.")

        buttons = [[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel:{intake.id}")]]
        if r.ok:
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


def build_application(settings: Settings) -> Application:
    catalog = Catalog.load()
    store = Store(settings.data_dir / "intake.sqlite3")
    vision = Vision(catalog, settings.claude_model)
    sheet = Sheet(settings.google_credentials_file, settings.spreadsheet_id, settings.worksheet)
    bot = IntakeBot(settings, catalog, store, vision, sheet)

    app = Application.builder().token(settings.telegram_token).build()
    app.add_handler(CommandHandler(["start", "help"], bot.start))
    app.add_handler(MessageHandler(filters.PHOTO, bot.photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.text))
    app.add_handler(CallbackQueryHandler(bot.button))
    return app
