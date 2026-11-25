"""Telegram bot: photo -> proposal -> confirm/correct -> sheet row.

Long-polling (Application.run_polling), so it needs no public URL — run it
as a service on any always-on machine.
"""

from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime, timezone

from zoneinfo import ZoneInfo

from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyKeyboardMarkup,
                      ReplyKeyboardRemove, Update)
from telegram.constants import ParseMode
from telegram.error import TimedOut
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

from .caption import apply_sale_price, parse_caption
from .catalog import Catalog
from .config import Settings
from .item import DEFAULT_QUANTITY, ItemFields, auto_descriptor, review, sample_rows, to_import_row
from .master import MasterUnavailable, ProductMaster, base_reference, match, parse_descriptor
from .naming import decode_cipher
from .lookup import MIN_PREFIX, build_prefix, format_result, parse_lookup
from .session import END_LABEL, greeting, summary as session_summary
from .sheet import Sheet
from .store import Intake, Store
from .group import (apply_change, count_check, descriptors_within_group, gender_from_codes, parse_command,
                    quick_change, split_caption)
from .vision import PhotoResult, Vision, VisionError

log = logging.getLogger(__name__)
ALBUM_WAIT_SECONDS = 2.0

MARKS_HELP = (
    "<b>What the marks mean</b> (you don't press them):\n"
    "📦 <b>Restock of existing …</b> — already in accounting; more stock. Tap ✅.\n"
    "🆕 <b>New product …</b> — not in accounting yet; created on import. Tap ✅.\n"
    "❓ next to a field — Claude wasn't sure from the photo. Check it; if wrong, reply e.g. <code>case gold</code>.\n"
    "❔ <b>Which product is this?</b> — tap the right product button (AC/EXP/CA).\n"
    "⚠️ <b>Product master not reachable</b> — couldn't check accounting this time; you can still confirm.\n\n"
    "To change something, reply to the summary in plain words. Buttons under the message: "
    "✅ Confirm · ❌ Cancel · 🔁 Replace · ➕ Add qty."
)

HELP = (
    "Send a photo of one item (add the tag/box as extra photos in the same album).\n"
    "Caption: brand, the price number, gender, SKU, and optionally strap/colors in tag order — e.g.\n"
    "<code>Seiko 1.250.000 sku SRPD53K1 G ss silver bd silver 1pcs</code>\n"
    "<code>AC 950k ref 1007 L leather black bd rose gold qty 2</code>\n"
    "• List brands (AC, EXP, Seiko, Casio…): type the distributor <b>list price</b>.\n"
    "• Nett brands (Bonia, Timberland…): type the <b>cost</b>.\n"
    "• SALE brands: type the selling price.\n"
    "Clocks: add <code>JD</code> or <code>clock</code>, e.g. <code>Seiko JD 250k sku QXA756W</code>.\n"
    "<b>Several watches of one model in one photo</b>: one caption for all, with the total as a check, e.g. "
    "<code>Ac 5014 semua warna 2.000.000 5 pcs</code>. Fix one by number: <code>3 dial black</code>.\n"
    "Then reply to my summary in plain words to fix anything (\"rubber not leather, ladies\"), "
    "and press ✅ when it's right."
)


class IntakeBot:
    def __init__(self, settings: Settings, catalog: Catalog, store: Store, vision: Vision, sheet: Sheet,
                 master: ProductMaster | None = None):
        self.s, self.catalog, self.store, self.vision, self.sheet = settings, catalog, store, vision, sheet
        self.master = master
        self.photo_dir = settings.data_dir / "photos"
        self.photo_dir.mkdir(parents=True, exist_ok=True)
        self._albums: dict[str, list[Message]] = {}
        self._waiting: dict[tuple[int, int], list[Message]] = {}  # photos sent with no session open
        self.tz = ZoneInfo(getattr(settings, "timezone", "Asia/Jakarta"))

    # ---------- menu and intake sessions ----------

    MENU = InlineKeyboardMarkup([[InlineKeyboardButton("📦 1. Intake", callback_data="menu:intake"),
                                  InlineKeyboardButton("🧰 2. Other tasks", callback_data="menu:other")]])
    SESSION_KEYS = ReplyKeyboardMarkup([[END_LABEL]], resize_keyboard=True, is_persistent=True)

    async def _show_menu(self, message: Message, first_name: str | None) -> None:
        await message.reply_text(greeting(first_name), parse_mode=ParseMode.HTML, reply_markup=self.MENU)

    async def _start_intake(self, message: Message, user) -> None:
        session = self.store.start_session(message.chat_id, user.id, user.full_name)
        started = datetime.fromisoformat(session.started_at).astimezone(self.tz)
        await message.reply_text(
            f"📦 <b>Intake session started</b> ({started:%H:%M}).\n\n{HELP}\n\n"
            f"When you're done, tap <b>{END_LABEL}</b> below (or send /end).",
            parse_mode=ParseMode.HTML, reply_markup=self.SESSION_KEYS)
        waiting = self._waiting.pop((message.chat_id, user.id), None)
        if waiting:
            await self._process(waiting, session.id)

    async def _end_requested(self, message: Message, user) -> None:
        session = self.store.active_session(message.chat_id, user.id)
        if not session:
            await message.reply_text("No intake session is open.", reply_markup=ReplyKeyboardRemove())
            await self._show_menu(message, user.first_name)
            return
        pending = [i for i in self.store.session_items(session.id) if i.status == "pending"]
        if pending:
            listing = "\n".join(f"• <code>{html.escape(i.id)}</code> {html.escape(i.fields.brand or '?')} "
                                 f"{html.escape(i.fields.manufacturer_sku or i.fields.ref_code or '')}"
                                 for i in pending)
            await message.reply_text(
                f"⏳ <b>{len(pending)} item(s) not confirmed yet</b> — they are NOT in the sheet:\n{listing}\n\n"
                "Confirm or cancel them first, or discard them and end.",
                parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 Show them", callback_data=f"end:review:{session.id}")],
                    [InlineKeyboardButton("🗑 Discard them & end", callback_data=f"end:discard:{session.id}")],
                    [InlineKeyboardButton("↩️ Keep receiving", callback_data=f"end:keep:{session.id}")]]))
            return
        await self._finish_session(message, session.id, user.first_name)

    async def _finish_session(self, message: Message, session_id: int, first_name: str | None) -> None:
        session = self.store.end_session(session_id)
        text = session_summary(session, self.store.session_items(session_id), self.tz, self.sheet.tab_title())
        await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=ReplyKeyboardRemove())
        await self._show_menu(message, first_name)

    async def _menu_or_end_button(self, query, user) -> None:
        kind, _, rest = query.data.partition(":")
        message = query.message
        if kind == "menu":
            await query.answer()
            if rest in ("intake", "intake_use"):
                await query.edit_message_reply_markup(None)
                await self._start_intake(message, user)
            elif rest == "other":
                await message.reply_text(
                    "🧰 <b>Other tasks</b>\n\n🔎 <b>Cek SKU / model</b>: type <code>cek SRPD53K1</code> or "
                    "<code>cek AC 5014</code> to see if it's in accounting, its stock, and what the bot has received.", parse_mode=ParseMode.HTML, reply_markup=self.MENU)
            elif rest == "dismiss":
                self._waiting.pop((message.chat_id, user.id), None)
                await query.edit_message_text("OK, photo not used.")
            return
        action, _, sid = rest.partition(":")
        session = self.store.session(int(sid)) if sid.isdigit() else None
        if not session or session.ended_at or session.user_id != user.id:
            await query.answer("This session is already closed.")
            return
        pending = [i for i in self.store.session_items(session.id) if i.status == "pending"]
        await query.answer()
        await query.edit_message_reply_markup(None)
        if action == "review":
            shown_groups = set()
            for i in pending:
                if i.group_id:
                    if i.group_id not in shown_groups:
                        shown_groups.add(i.group_id)
                        await self._send_group_summary(message, i.group_id)
                else:
                    await self._send_summary(message, i)
            await message.reply_text(f"Confirm or cancel each one, then tap {END_LABEL} again.",
                                     reply_markup=self.SESSION_KEYS)
        elif action == "discard":
            for i in pending:
                self.store.update(i.id, status="cancelled")
                if i.group_id:
                    self.store.update_group(i.group_id, status="cancelled")
            await self._finish_session(message, session.id, user.first_name)
        else:
            await message.reply_text("OK, keep sending photos.", reply_markup=self.SESSION_KEYS)

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
        if not await self._allowed(update):
            return
        msg, user = update.effective_message, update.effective_user
        if self.store.active_session(msg.chat_id, user.id):
            await msg.reply_text(f"📦 Your intake session is still open.\n\n{HELP}\n\n"
                                 f"Tap <b>{END_LABEL}</b> when you're done.",
                                 parse_mode=ParseMode.HTML, reply_markup=self.SESSION_KEYS)
        else:
            await self._show_menu(msg, user.first_name)

    async def lookup_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._allowed(update):
            await self._lookup(update.effective_message, " ".join(context.args or []))

    async def _lookup(self, msg: Message, query: str) -> None:
        """Read-only: is this reference in accounting, with what stock, and what
        has the bot received for it."""
        if not query:
            await msg.reply_text("Type the SKU or model after cek, e.g. <code>cek SRPD53K1</code> or "
                                 "<code>cek AC 5014</code>.", parse_mode=ParseMode.HTML)
            return
        prefix, label = build_prefix(self.catalog, query)
        if len(prefix) < MIN_PREFIX:
            await msg.reply_text(f"Type at least {MIN_PREFIX} characters of the code.")
            return
        products = None
        if self.master:
            try:
                products = await asyncio.to_thread(self.master.by_prefix, prefix)
            except MasterUnavailable as err:
                log.warning("Lookup: %s", err)
        try:
            rows = await asyncio.to_thread(self.sheet._history)
            receipts = [r for r in rows if str(r.get("preview_sku", "")).upper().startswith(prefix)]
        except Exception:
            log.exception("Lookup: sheet history failed")
            receipts = None
        await msg.reply_text(format_result(label, prefix, products, receipts), parse_mode=ParseMode.HTML)

    async def end_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._allowed(update):
            await self._end_requested(update.effective_message, update.effective_user)

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
        await self._photos_in(([msg]))

    async def _album_ready(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        messages = self._albums.pop(context.job.data, [])
        if messages:
            await self._photos_in(messages)

    async def _photos_in(self, messages: list[Message]) -> None:
        first = messages[0]
        session = self.store.active_session(first.chat_id, first.from_user.id)
        if session:
            await self._process(messages, session.id)
            return
        self._waiting[(first.chat_id, first.from_user.id)] = messages
        await first.reply_text(
            "No intake session is open. Start one and use this photo?",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📦 Start intake & use it", callback_data="menu:intake_use"),
                InlineKeyboardButton("Not now", callback_data="menu:dismiss")]]))

    async def _process(self, messages: list[Message], session_id: int | None = None) -> None:
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
            result = await asyncio.to_thread(self.vision.propose_items, images, caption)
        except VisionError as e:
            result = PhotoResult(count=1, count_sure=True, items=[ItemFields(notes=f"Photo not read: {e}")], notes="")

        if len(result.items) > 1:
            await status.delete()
            await self._start_group(first, user, session_id, intake_id, result, caption, paths)
            return
        proposed = result.items[0]
        fields = apply_sale_price(self.catalog, proposed.merged(parse_caption(self.catalog, caption)))
        intake = self.store.create(intake_id, first.chat_id, user.id, user.full_name, fields, paths, session_id)
        await status.delete()
        await self._send_summary(first, intake)

    async def text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._allowed(update):
            return
        msg, user = update.effective_message, update.effective_user
        typed = (msg.text or "").strip()
        if typed == END_LABEL or typed.lower() in ("/end", "end intake", "selesai"):
            await self._end_requested(msg, user)
            return
        query = parse_lookup(typed)
        if query is not None:
            await self._lookup(msg, query)
            return
        if not any(ch.isalnum() for ch in typed) or typed.lower() in ("help", "bantuan", "?", "apa", "how"):
            # "?", "??", "👍", "help": explain, never send to Claude as an edit.
            await msg.reply_text(MARKS_HELP, parse_mode=ParseMode.HTML)
            return
        session = self.store.active_session(msg.chat_id, user.id)
        if not session and typed.lower() in ("1", "intake", "1. intake"):
            await self._start_intake(msg, user)
            return
        if not session and typed.lower() in ("2", "other", "other tasks"):
            await msg.reply_text("🧰 <b>Other tasks</b>\n\n🔎 <b>Cek SKU / model</b>: type <code>cek SRPD53K1</code> or "
                    "<code>cek AC 5014</code> to see if it's in accounting, its stock, and what the bot has received.", parse_mode=ParseMode.HTML,
                                 reply_markup=self.MENU)
            return
        group = None
        if msg.reply_to_message:
            group = self.store.group_by_message(msg.chat_id, msg.reply_to_message.message_id)
        if not group:
            pending_group = self.store.latest_pending_group(msg.chat_id, user.id)
            if pending_group and (parse_command(typed) or not self.store.latest_pending(msg.chat_id, user.id)):
                group = pending_group
        if group and group.status == "pending":
            await self._group_text(msg, group.id, typed)
            return
        intake = None
        if msg.reply_to_message:
            intake = self.store.by_message(msg.chat_id, msg.reply_to_message.message_id)
        intake = intake or self.store.latest_pending(msg.chat_id, msg.from_user.id)
        if not intake or intake.status != "pending":
            if session:
                await msg.reply_text(f"Nothing pending to correct. Send the next photo, or tap {END_LABEL}.",
                                     reply_markup=self.SESSION_KEYS)
            else:
                await self._show_menu(msg, user.first_name)
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
        if query.data.startswith(("menu:", "end:")):
            await self._menu_or_end_button(query, update.effective_user)
            return
        if query.data.startswith("g:"):
            await self._group_button(query)
            return
        action, intake_id = query.data.split(":", 1)
        picked = None
        if action == "pick":
            intake_id, picked = intake_id.split(":", 1)
        intake = self.store.get(intake_id)
        if not intake or intake.status != "pending":
            await query.answer("Already handled.")
            return

        if action == "pick":
            await query.answer("Got it.")
            await self._apply_pick(query.message, intake, picked)
            return

        if action == "cancel":
            self.store.update(intake.id, status="cancelled")
            await query.answer("Cancelled.")
            await query.edit_message_reply_markup(None)
            await query.message.reply_text(f"❌ {intake.id} cancelled — nothing written.")
            return

        status, text = await self._save_item(intake, action)
        if status == "invalid":
            await query.answer("Still has problems — see the list.", show_alert=True)
            return
        if status == "conflict":
            await query.answer("Already received today: choose Replace or Add qty.", show_alert=True)
            return
        await query.answer("Saved.")
        await query.edit_message_reply_markup(None)
        await query.message.reply_text(text)

    async def _save_item(self, intake: Intake, action: str) -> tuple[str, str]:
        """Write one confirmed item to the sheet. action: confirm | replace | addqty.
        Returns ("saved" | "conflict" | "invalid", message). "conflict" means the
        same SKU was already received today and staff must choose replace/addqty."""
        r = review(self.catalog, intake.fields)
        if not r.ok:
            return "invalid", ""
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
                saved_action, status = "replaced", "confirmed"
                # The replaced item no longer counts in its own session's summary.
                old = self.store.get(str(existing.get("intake_id", "")))
                if old and old.id != intake.id and old.saved_action:
                    self.store.record_saved(old.id, status="replaced", action="", qty=0, name=old.saved_name or "",
                                            sku=old.saved_sku or "", hbb=old.saved_hbb, hjb=old.saved_hjb)
            else:
                new_qty = await asyncio.to_thread(self.sheet.add_quantity, existing, qty, f"+{qty} ({intake.id})")
                text = f"➕ Added {qty} to today's {existing.get('intake_id', '?')} for {r.sku}: now {new_qty} pcs."
                saved_action, status = "merged", "merged"
            self.store.record_saved(intake.id, status=status, action=saved_action, qty=qty, name=r.name or "",
                                    sku=r.sku or "", hbb=r.hbb, hjb=r.hjb)
        elif action == "confirm" and today_rows:
            return "conflict", ""
        else:
            # New receipt line (a different date counts as a new receipt).
            await asyncio.to_thread(self.sheet.append, row)
            self.store.record_saved(intake.id, status="confirmed", action="added", qty=qty, name=r.name or "",
                                    sku=r.sku or "", hbb=r.hbb, hjb=r.hjb)
            text = f"✅ {intake.id} received ({qty} pcs) on {row['receipt_date']}, added to {self.sheet.tab_title()}."
        return "saved", f"{text}\n🏷 {r.name or ''}\n🔖 {r.sku or ''}"

    async def _apply_pick(self, message: Message, intake: Intake, picked: str) -> None:
        """Staff chose which existing product (or a new variant) this is."""
        f = intake.fields
        base = base_reference(self.catalog, f) or ""
        data = f.model_dump()
        sku = picked[len("new: "):] if picked.startswith("new: ") else picked
        parsed = parse_descriptor(self.catalog, sku[len(base):]) if sku.upper().startswith(base.upper()) else None
        if parsed is None:
            await message.reply_text("That choice no longer fits this item. Please check the details again.")
            return
        dial, index, crown = parsed
        data.update(dial_color=dial.name if dial else None, index_type=index.name if index else None,
                    crown_color=crown.name if crown else None,
                    reference_choice=sku if dial is None else None)
        self.store.update(intake.id, fields=ItemFields(**data))
        await self._send_summary(message, self.store.get(intake.id))

    # ---------- multi-item photos ----------

    async def _start_group(self, first: Message, user, session_id: int | None, base_id: str,
                           result: PhotoResult, caption: str | None, paths: list[str]) -> None:
        cap, total = split_caption(parse_caption(self.catalog, caption))
        items = [apply_sale_price(self.catalog, seen.merged(cap)) for seen in result.items]
        gender_note = None
        if not items[0].gender:
            gender, gender_note = await self._suggest_gender(items[0])
            if gender:
                items = [apply_change(f, {"gender": gender}) for f in items]
        self.store.create_group(base_id, first.chat_id, user.id, session_id, result.count, result.count_sure,
                                total, result.notes or None)
        if gender_note:
            self.store.update_group(base_id, gender_note=gender_note)
        for pos, f in enumerate(items, start=1):
            self.store.create(f"{base_id}-{pos}", first.chat_id, user.id, user.full_name, f, paths, session_id,
                              group_id=base_id, position=pos)
        await self._send_group_summary(first, base_id)

    async def _suggest_gender(self, f: ItemFields) -> tuple[str | None, str | None]:
        """Gender is never read from a photo. For an AC/EXP/CA model, propose
        the one gender its existing references already use (product master,
        then this sheet's history)."""
        brand = self.catalog.brands.get(f.brand)
        if not (brand and brand.uses_generated_sku and f.ref_code):
            return None, None
        prefix = f"{brand.code}{f.ref_code}".upper()
        label = f"{brand.code} {f.ref_code}"
        if self.master:
            try:
                products = await asyncio.to_thread(self.master.by_prefix, prefix)
                if g := gender_from_codes(prefix, [p.sku for p in products]):
                    return g, f"from existing {label} products"
            except MasterUnavailable:
                pass
        try:
            rows = await asyncio.to_thread(self.sheet._history)
            if g := gender_from_codes(prefix, [str(r.get("preview_sku", "")) for r in rows]):
                return g, f"from earlier {label} receipts"
        except Exception:
            log.exception("Gender lookup in the sheet failed")
        return None, None

    async def _prepare_group(self, group_id: str) -> tuple[list[Intake], dict[int, int], dict[str, str], dict[str, list[str]]]:
        """Descriptors within the photo, then the product master check per item.
        Returns (pending items, clashes {pos: other pos}, notes {id: note}, choices {id: options})."""
        items = [i for i in self.store.group_items(group_id) if i.status == "pending"]
        fixed, _ = descriptors_within_group(self.catalog, [i.fields for i in items])
        for i, f in zip(items, fixed):
            if f != i.fields:
                self.store.update(i.id, fields=f)
        notes, choices = {}, {}
        items = [self.store.get(i.id) for i in items]
        for n, i in enumerate(items):
            i2, note, opts = await self._resolve_reference(i)
            items[n] = i2
            if note:
                notes[i2.id] = note
            if opts:
                choices[i2.id] = opts
        _, clashes_idx = descriptors_within_group(self.catalog, [i.fields for i in items])
        clashes = {items[a].position: items[c].position for a, c in clashes_idx.items()}
        return items, clashes, notes, choices

    async def _send_group_summary(self, reply_to: Message, group_id: str) -> None:
        group = self.store.group(group_id)
        items, clashes, notes, choices = await self._prepare_group(group_id)
        e = html.escape
        if not items:
            self.store.update_group(group_id, status="done")
            await reply_to.reply_text("Nothing left in this group.")
            return
        first = items[0].fields
        r0 = review(self.catalog, first)
        lines = [f"🧺 <b>Group {e(group_id)}</b>: {e(first.brand or '?')} {e(first.ref_code or first.manufacturer_sku or '')}"
                 f" — {len(items)} item(s)"]
        if r0.hjb:
            price = f"HLB/price Rp{(first.supplied_price or first.selling_price or 0):,.0f} each · "
            lines.append(f"💰 {price}{'HBB Rp%s · ' % format(r0.hbb, ',.0f') if r0.hbb is not None else ''}HJB Rp{r0.hjb:,.0f}")
        hint = self._tag_hint(group.notes or "", r0.hjb) if group.notes else None
        if hint:
            lines.append(hint)
        genders = {i.fields.gender for i in items}
        if None in genders:
            lines.append("❗ <b>Gender needed for all</b> — tap below (never read from the photo).")
        else:
            src = f" ({e(group.gender_note)})" if group.gender_note else ""
            lines.append(f"Gender: <b>{e(', '.join(sorted(g for g in genders if g)))}</b>{src}")
        count_ok, count_line = count_check(group.seen_count, bool(group.count_sure), group.caption_total,
                                           bool(group.count_ok), [i.fields for i in items])
        lines += [count_line, ""]
        all_ok = count_ok and None not in genders
        for i in items:
            f, r = i.fields, review(self.catalog, i.fields)
            parts = [x for x in (self.catalog.strap_types.get(f.strap_type) and self.catalog.strap_types.get(f.strap_type).code,
                                 f.strap_color, "Bd", f.case_color) if x]
            seen = f" · dial {e(f.photo_dial_color)}" if f.photo_dial_color else ""
            unsure = " ❓" if f.uncertain else ""
            qty = f" ×{f.quantity}" if f.quantity is not None and f.quantity != 1 else ""
            tag = "📦" if "Restock" in notes.get(i.id, "") else "🆕" if "New product" in notes.get(i.id, "") else ""
            lines.append(f"<b>{i.position}.</b> {e(' '.join(parts))}{seen}{unsure}{qty} {tag}")
            if r.name:
                lines.append(f"    → <code>{e(r.name)}</code>")
            problems = [err for err in r.errors if not err.startswith("gender")]
            if i.position in clashes:
                problems.append(f"same code as item {clashes[i.position]}: if it's the same watch, "
                                f"\"hapus {i.position}\" and \"{clashes[i.position]} qty 2\"; else give its dial")
            if i.id in choices:
                problems.append("which existing product? reply e.g. \"%d dial blue\"" % i.position)
            for p in problems:
                lines.append(f"    ⚠️ {e(p)}")
            if problems:
                all_ok = False
        lines += ["", "Fix one watch by its number: <code>3 dial black</code>, <code>2 qty 3</code>, "
                      "<code>hapus 5</code>. For all: <code>semua L</code>."]
        buttons: list[list[InlineKeyboardButton]] = []
        if None in genders:
            buttons.append([InlineKeyboardButton(t, callback_data=f"g:{group_id}:gender:{c}")
                            for t, c in (("G", "Gents"), ("L", "Ladies"), ("G/L", "Gents/Ladies"))])
        if not count_ok:
            pcs = sum(i.fields.quantity if i.fields.quantity is not None else DEFAULT_QUANTITY for i in items)
            buttons.append([InlineKeyboardButton(f"✅ {len(items)} items / {pcs} pcs is right",
                                                 callback_data=f"g:{group_id}:countok")])
        row = [InlineKeyboardButton("❌ Cancel all", callback_data=f"g:{group_id}:cancel")]
        if all_ok:
            row.insert(0, InlineKeyboardButton(f"✅ Confirm all {len(items)}", callback_data=f"g:{group_id}:confirm"))
        buttons.append(row)
        sent = await reply_to.reply_text("\n".join(lines), parse_mode=ParseMode.HTML,
                                         reply_markup=InlineKeyboardMarkup(buttons))
        if group.message_id:
            try:
                await reply_to.get_bot().edit_message_reply_markup(group.chat_id, group.message_id, reply_markup=None)
            except Exception:
                pass
        self.store.update_group(group_id, message_id=sent.message_id)

    async def _group_text(self, msg: Message, group_id: str, typed: str) -> None:
        cmd = parse_command(typed)
        items = {i.position: i for i in self.store.group_items(group_id) if i.status == "pending"}
        if not cmd:
            await msg.reply_text("Start with the item number to fix one watch (e.g. <code>3 dial black</code>), "
                                 "or <code>semua …</code> for all.", parse_mode=ParseMode.HTML)
            return
        kind, n, rest = cmd
        if kind in ("item", "remove") and n not in items:
            await msg.reply_text(f"There's no item {n} in this group.")
            return
        if kind == "remove":
            self.store.update(items[n].id, status="cancelled")
        else:
            targets = [items[n]] if kind == "item" else list(items.values())
            change = quick_change(rest)
            for i in targets:
                if change:
                    fields = apply_change(i.fields, change)
                else:
                    try:
                        fields = await asyncio.to_thread(self.vision.correct, i.fields, rest)
                    except VisionError as err:
                        await msg.reply_text(f"Couldn't apply that to item {i.position}: {err}")
                        return
                self.store.update(i.id, fields=apply_sale_price(self.catalog, fields))
        await self._send_group_summary(msg, group_id)

    async def _group_button(self, query) -> None:
        _, group_id, action, *arg = query.data.split(":")
        group = self.store.group(group_id)
        if not group or group.status != "pending":
            await query.answer("Already handled.")
            return
        items = [i for i in self.store.group_items(group_id) if i.status == "pending"]
        if action == "gender":
            for i in items:
                self.store.update(i.id, fields=apply_change(i.fields, {"gender": arg[0]}))
            self.store.update_group(group_id, gender_note=None)
            await query.answer(arg[0])
            await self._send_group_summary(query.message, group_id)
        elif action == "countok":
            self.store.update_group(group_id, count_ok=1)
            await query.answer("OK")
            await self._send_group_summary(query.message, group_id)
        elif action == "cancel":
            for i in items:
                self.store.update(i.id, status="cancelled")
            self.store.update_group(group_id, status="cancelled")
            await query.answer("Cancelled.")
            await query.edit_message_reply_markup(None)
            await query.message.reply_text(f"❌ Group {group_id} cancelled — nothing written.")
        elif action in ("confirm", "confirm_add", "confirm_replace"):
            per_conflict = {"confirm": "confirm", "confirm_add": "addqty", "confirm_replace": "replace"}[action]
            if action == "confirm":
                conflicts = []
                for i in items:
                    sku = review(self.catalog, i.fields).sku or ""
                    if await asyncio.to_thread(self.sheet.same_sku_today, sku):
                        conflicts.append(i.position)
                if conflicts:
                    await query.answer()
                    await query.message.reply_text(
                        f"🔁 Item(s) {', '.join(map(str, conflicts))} were already received <b>today</b>. "
                        "Add these to today's lines, or replace them?", parse_mode=ParseMode.HTML,
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("➕ Add qty", callback_data=f"g:{group_id}:confirm_add"),
                            InlineKeyboardButton("🔁 Replace", callback_data=f"g:{group_id}:confirm_replace"),
                            InlineKeyboardButton("❌ Cancel all", callback_data=f"g:{group_id}:cancel")]]))
                    return
            await query.answer("Saving…")
            await query.edit_message_reply_markup(None)
            done, problems = [], []
            for i in items:
                status, text = await self._save_item(i, "confirm")
                if status == "conflict":
                    status, text = await self._save_item(i, per_conflict)
                (done if status == "saved" else problems).append((i, text))
            pcs = sum(int(review(self.catalog, i.fields).ok and (i.fields.quantity or DEFAULT_QUANTITY)) for i, _ in done)
            lines = [f"✅ Group {group_id}: {len(done)} item(s), {pcs} pcs saved to {self.sheet.tab_title()}."]
            for i, _ in done:
                r = review(self.catalog, i.fields)
                lines.append(f"{i.position}. {r.name}  ·  {r.sku}")
            if problems:
                lines.append(f"⚠️ Not saved: {', '.join(str(i.position) for i, _ in problems)} — check them.")
            else:
                self.store.update_group(group_id, status="done")
            await query.message.reply_text("\n".join(lines))

    # ---------- summary ----------

    async def _resolve_reference(self, intake: Intake) -> tuple[Intake, str | None, list[str]]:
        """Which product is this? Returns (intake, note, options). `options`
        is non-empty when staff must pick the product (no Confirm until then).

        With the accounting API configured, checks the product master: the
        importer restocks a reference it finds there and creates one it
        doesn't, so for AC/EXP/CA the descriptor (part of the reference) must
        be chosen here. Without it, falls back to the sheet's month tabs."""
        f = intake.fields
        if not review(self.catalog, f).ok:
            return intake, None, []
        if self.master:
            try:
                base = base_reference(self.catalog, f)
                if base:
                    products = await asyncio.to_thread(self.master.by_prefix, base)
                    m = match(self.catalog, f, products, base)
                    if m.fields != f:
                        self.store.update(intake.id, fields=m.fields)
                        intake = self.store.get(intake.id)
                    if m.kind in ("new", "restock"):
                        self.store.set_outcome(intake.id, m.kind)
                    if m.kind == "choose":
                        return intake, ("❔ <b>Which product is this?</b> Several existing AC/EXP/CA variants "
                                        "share this ref and strap/colors. Pick one below, or reply with its "
                                        "dial (e.g. \"dial blue\")."), m.options
                    return intake, m.note, []
                sku = review(self.catalog, f).sku
                if sku:
                    products = await asyncio.to_thread(self.master.by_prefix, sku)
                    hit = next((p for p in products if p.sku.upper() == sku.upper()), None)
                    self.store.set_outcome(intake.id, "restock" if hit else "new")
                    if hit:
                        qty = f", stock {hit.stock_quantity:g}" if isinstance(hit.stock_quantity, (int, float)) else ""
                        return intake, f"📦 Restock of existing <code>{html.escape(hit.sku)}</code>{qty}.", []
                    return intake, f"🆕 New product <code>{html.escape(sku)}</code>.", []
                return intake, None, []
            except MasterUnavailable as e:
                log.warning("%s; falling back to the sheet", e)
                fallback = "⚠️ Product master not reachable; checked this sheet's history instead."
        else:
            fallback = None
        intake, note = await self._apply_auto_descriptor(intake)
        return intake, " ".join(x for x in (fallback, note) if x) or None, []

    async def _apply_auto_descriptor(self, intake: Intake) -> tuple[Intake, str | None]:
        """Fallback without the product master: if this item would print the
        same name as a row in the sheet, add a descriptor from what Claude saw."""
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
        intake, descriptor_note, choices = await self._resolve_reference(intake)
        f = intake.fields
        r = review(self.catalog, f)
        e = html.escape

        marked: list[str] = []  # fields actually shown with ❓

        def q(key: str) -> str:
            if key in f.uncertain:
                marked.append(key)
                return " ❓"
            return ""

        def line(label: str, value, key: str | None = None) -> str:
            mark = q(key) if key and value not in (None, "") else ""
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
        seen = " · ".join(x for x in (
            f.photo_dial_color and f"dial {e(f.photo_dial_color)}{q('photo_dial_color')}",
            f.photo_index_type and f"{e(f.photo_index_type)} index{q('photo_index_type')}") if x)
        if seen:
            lines.append(f"👁 Seen: {seen}")
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
        if r.ok and not choices:
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
            elif dupes and not self.master:
                last = dupes[-1]
                lines.append(f"⚠️ Same name as {e(str(last.get('intake_id', '?')))} (received "
                             f"{e(str(last.get('receipt_date') or last.get('_tab', '?')))}). Same model → this is a "
                             "restock, fine. A different model → reply with a descriptor, e.g. \"dial blue\".")
        if descriptor_note:
            lines.append(descriptor_note)
        if marked:
            lines.append("❓ = not sure from the photo. Check it; if it's wrong, reply e.g. \"case gold\".")
        if r.errors:
            lines.append("\n<b>Needed before saving:</b>")
            lines += [f"• {e(err)}" for err in r.errors]
        lines.append("\nReply to this message to correct anything.")

        buttons = [[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel:{intake.id}")]]
        if r.ok and choices:
            buttons = [[InlineKeyboardButton(opt.replace("new: ", "🆕 "), callback_data=f"pick:{intake.id}:{opt}")]
                       for opt in choices] + buttons
        elif r.ok and same_sku:
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
    master = (ProductMaster(settings.accounting_api_url, settings.accounting_api_token)
              if settings.accounting_api_url and settings.accounting_api_token else None)
    if not master:
        log.warning("ACCOUNTING_API_URL/TOKEN not set: checking restocks against the sheet only")
    bot = IntakeBot(settings, catalog, store, vision, sheet, master)
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
    app.add_handler(CommandHandler(["start", "help", "menu"], bot.start))
    app.add_handler(CommandHandler(["end", "selesai"], bot.end_command))
    app.add_handler(CommandHandler(["cek", "check"], bot.lookup_command))
    app.add_handler(MessageHandler(filters.PHOTO, bot.photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.text))
    app.add_handler(CallbackQueryHandler(bot.button))
    app.add_error_handler(on_error)
    return app
