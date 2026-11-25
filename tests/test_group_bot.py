"""Multi-item photo flow through the bot, with fake Telegram / Claude / sheet / master."""

import asyncio
import re
from datetime import date
from types import SimpleNamespace as NS

from intake_bot.bot import IntakeBot
from intake_bot.catalog import Catalog
from intake_bot.item import ItemFields
from intake_bot.master import Product
from intake_bot.store import Store
from intake_bot.vision import PhotoResult
from tests.test_sheet import FakeSheet

USER = NS(id=2, first_name="Ani", full_name="Ani")


class Msg:
    out: list = []
    _n = 0

    def __init__(self, text=None, caption=None, photo=False):
        Msg._n += 1
        self.message_id, self.chat_id, self.text, self.caption = Msg._n, 1, text, caption
        self.from_user, self.reply_to_message, self.media_group_id = USER, None, None
        self.photo = [NS(get_file=self._file)] if photo else None

    async def _file(self):
        async def dl():
            return bytearray(b"\xff\xd8jpeg")
        return NS(download_as_bytearray=dl)

    async def reply_text(self, text, reply_markup=None, **kw):
        rows = getattr(reply_markup, "inline_keyboard", None) if reply_markup else None
        Msg.out.append((re.sub("<[^>]+>", "", text), [b.text for r in rows for b in r] if rows else []))
        return Msg()

    async def delete(self):
        pass

    async def _noop(self, *a, **k):
        pass

    def get_bot(self):
        return NS(edit_message_reply_markup=self._noop)


def seen(strap, scol, case, dial):
    return ItemFields(type="watch", brand="Alexandre Christie", strap_type=strap, strap_color=scol,
                      case_color=case, photo_dial_color=dial)


FIVE = [seen("Stainless St.", "Rose Gold", "Rose Gold", "Rose Gold"),
        seen("Stainless St.", "Rose Gold", "Rose Gold", "Brown"),
        seen("Rantai Kombi", "Rose Gold", "Silver", "Brown"),
        seen("Rantai Kombi", "Rose Gold", "Silver", "White"),
        seen("Rantai Kombi", "Rose Gold", "Silver", "Rose Gold")]


def make_bot(tmp_path, items, sure=True, master=None):
    class Vision:
        def propose_items(self, images, caption):
            return PhotoResult(len(items), sure, list(items), "tag cipher: UOOO")
    st = Store(tmp_path / "x.sqlite3")
    sheet = FakeSheet(date(2026, 11, 25))
    bot = IntakeBot(NS(allowed_user_ids={2}, data_dir=tmp_path, timezone="Asia/Jakarta"), Catalog.load(), st,
                    Vision(), sheet, master)
    st.start_session(1, 2, "Ani")
    Msg.out.clear()
    return bot, st, sheet


def run(coro):
    asyncio.run(coro)


def upd(m):
    return NS(effective_message=m, effective_user=USER)


def press(bot, data):
    m = Msg()

    async def ans(*a, **k):
        pass
    run(bot.button(NS(callback_query=NS(data=data, answer=ans, edit_message_reply_markup=m._noop, message=m),
                      effective_user=USER, effective_message=m), None))


def last():
    return Msg.out[-1]


def test_five_watches_one_caption(tmp_path):
    master = NS(by_prefix=lambda p: [x for x in [Product("AC5014SS20C20Ldi11", stock_quantity=2)]
                                     if x.sku.upper().startswith(p.upper())])
    bot, st, sheet = make_bot(tmp_path, FIVE, master=master)
    run(bot.photo(upd(Msg(caption="Ac 5014\nSemua warna 2.000.000\n6 pcs", photo=True)), None))
    text, keys = last()
    assert "Gender: Ladies (from existing AC 5014 products)" in text
    assert "Caption says 6 pcs but the items add up to 5" in text and not any("Confirm" in k for k in keys)
    assert "AC 5014 DT RSG Bd SIL diBRN. L - UOOO" in text and "Tag UOOO matches" in text
    run(bot.text(upd(Msg("2 qty 2")), None))
    text, keys = last()
    assert "✔ 6 pcs = caption 6 pcs" in text and "✅ Confirm all 5" in keys
    press(bot, f"g:{st.latest_pending_group(1, 2).id}:confirm")
    rows = sheet.current().get_all_records()
    assert [(r["preview_sku"], r["quantity"]) for r in rows] == [
        ("AC5014SS20C20Ldi20", 1), ("AC5014SS20C20Ldi11", 2), ("AC5014DT20C18Ldi11", 1),
        ("AC5014DT20C18Ldi01", 1), ("AC5014DT20C18Ldi20", 1)]
    assert "5 item(s), 6 pcs saved" in last()[0]


def test_no_gender_record_unsure_count_and_remove(tmp_path):
    bot, st, sheet = make_bot(tmp_path, FIVE[:3], sure=False)
    run(bot.photo(upd(Msg(caption="AC 5014 2.000.000", photo=True)), None))
    text, keys = last()
    assert "Gender needed for all" in text and {"G", "L", "G/L"} <= set(keys)
    assert "I count 3 item(s) but I'm not sure" in text
    gid = st.latest_pending_group(1, 2).id
    press(bot, f"g:{gid}:gender:Ladies")
    run(bot.text(upd(Msg("hapus 3")), None))
    text, keys = last()
    assert "— 2 item(s)" in text and "✅ 2 items / 2 pcs is right" in keys
    press(bot, f"g:{gid}:countok")
    assert "✅ Confirm all 2" in last()[1]
    press(bot, f"g:{gid}:confirm")
    assert len(sheet.current().get_all_records()) == 2


def test_single_photo_still_uses_the_single_flow(tmp_path):
    one = [ItemFields(type="watch", brand="Seiko", strap_type="Stainless St.", strap_color="Silver",
                      case_color="Silver")]
    bot, st, sheet = make_bot(tmp_path, one)
    run(bot.photo(upd(Msg(caption="Seiko 1.250.000 sku SRPD53K1 G", photo=True)), None))
    text, keys = last()
    assert text.startswith("Item ") and "✅ Confirm" in keys and st.latest_pending_group(1, 2) is None
