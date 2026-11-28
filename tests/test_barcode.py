"""Barcodes: decoding, typed values, Claude's digits only with a valid check digit,
and the barcode column written at End intake."""

import io

import pytest

import intake_bot.bot as botmod
from intake_bot.barcode import choose, clean, from_text, gtin_ok, looks_mistyped, scan
from intake_bot.caption import parse_caption
from intake_bot.catalog import Catalog
from intake_bot.group import quick_change
from intake_bot.item import ItemFields
from intake_bot.sheet import HEADER
from tests.test_group_bot import FIVE, Msg, last, make_bot, run, upd
from tests.test_revoke import seiko

EAN = "4901234567894"     # valid EAN-13
EAN2 = "4954628422318"    # valid EAN-13
BAD = "4901234567895"     # wrong check digit


def test_check_digit_and_cleaning():
    assert gtin_ok(EAN) and gtin_ok("96385074") and gtin_ok("036000291452") and not gtin_ok(BAD)
    assert clean("4 901234-567894") == EAN and clean("abc12345") == "ABC12345" and clean("12") is None
    assert looks_mistyped(BAD) and not looks_mistyped(EAN) and not looks_mistyped("123456")


def test_caption_barcode_is_not_read_as_price_or_sku():
    f = parse_caption(Catalog.load(), f"Seiko 1.250.000 sku SRPD53K1 barcode {EAN} G ss silver bd silver")
    assert (f.barcode, f.manufacturer_sku, f.supplied_price, f.gender, f.strap_color) == \
        (EAN, "SRPD53K1", 1_250_000, "Gents", "Silver")
    assert parse_caption(Catalog.load(), "Seiko 1.250.000 sku SRPD53K1").barcode is None
    assert from_text("bc: 4 901234 567894")[0] == EAN


def test_quick_change_sets_barcode_alone_or_with_others():
    assert quick_change(f"barcode {EAN}") == {"barcode": EAN}
    assert quick_change(f"bc {EAN} L") == {"barcode": EAN, "gender": "Ladies"}


def test_choose_prefers_the_decoder_and_checks_claudes_digits():
    assert choose([EAN], "") == (EAN, False, None)
    assert choose([], EAN) == (EAN, True, None)                    # read by eye: ❓
    assert choose([], BAD) == (None, False, None)                  # misread digit: blank, not guessed
    code, _, note = choose([EAN, EAN2], EAN)
    assert code is None and EAN2 in note


def test_decodes_a_photographed_ean13():
    zxingcpp = pytest.importorskip("zxingcpp")
    Image = pytest.importorskip("PIL.Image")
    numpy = pytest.importorskip("numpy")  # only to turn zxing's test image into a PIL one
    bars = zxingcpp.write_barcode_to_image(zxingcpp.create_barcode(EAN, zxingcpp.BarcodeFormat.EAN13), scale=3)
    photo = Image.new("RGB", (1280, 960), "white")
    photo.paste(Image.fromarray(numpy.array(bars)).convert("RGB").rotate(90, expand=True), (700, 300))
    buf = io.BytesIO()
    photo.save(buf, "JPEG", quality=70)
    assert scan([buf.getvalue(), b"not an image"]) == [EAN]


def test_single_item_barcode_shown_and_written_at_end(tmp_path, monkeypatch):
    monkeypatch.setattr(botmod, "scan_barcodes", lambda images: [EAN])
    bot, st, sheet = make_bot(tmp_path, seiko())
    run(bot.photo(upd(Msg(caption="Seiko 1.250.000 sku SRPD53K1 G", photo=True)), None))
    assert f"Barcode: {EAN}" in last()[0]
    run(bot.text(upd(Msg(f"barcode {EAN2}")), None))               # staff override, no Claude call
    item = st.latest_pending(1, 2)
    assert item.fields.barcode == EAN2 and f"Barcode: {EAN2}" in last()[0]
    run(bot.button(_press(f"confirm:{item.id}"), None))
    assert sheet.current().get_all_records() == []                 # staged only
    run(bot.text(upd(Msg("⏹ End intake")), None))
    rows = sheet.current().get_all_records()
    assert HEADER[-1] == "barcode" and rows[0]["barcode"] == EAN2


def test_no_barcode_stays_blank(tmp_path, monkeypatch):
    monkeypatch.setattr(botmod, "scan_barcodes", lambda images: [])
    bot, st, sheet = make_bot(tmp_path, seiko())
    run(bot.photo(upd(Msg(caption="Seiko 1.250.000 sku SRPD53K1 G", photo=True)), None))
    assert "Barcode: — (none found" in last()[0]
    run(bot.button(_press(f"confirm:{st.latest_pending(1, 2).id}"), None))
    run(bot.text(upd(Msg("⏹ End intake")), None))
    assert sheet.current().get_all_records()[0]["barcode"] == ""


def test_group_keeps_only_checked_codes_and_lists_unplaced_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(botmod, "scan_barcodes", lambda images: [EAN, EAN2])
    items = [f.model_copy() for f in FIVE]
    items[1] = items[1].model_copy(update={"barcode": EAN})        # Claude placed it; decoder confirms
    items[2] = items[2].model_copy(update={"barcode": BAD})        # misread: dropped
    bot, st, sheet = make_bot(tmp_path, items)
    run(bot.photo(upd(Msg(caption="Ac 5014 2.000.000 L 5 pcs", photo=True)), None))
    group_items = sorted(st.session_items(st.active_session(1, 2).id), key=lambda i: i.position)
    assert [i.fields.barcode for i in group_items] == [None, EAN, None, None, None]
    assert "barcode" not in group_items[1].fields.uncertain
    assert f"2 barcode {EAN2}" in last()[0]
    run(bot.text(upd(Msg(f"3 barcode {EAN2}")), None))
    assert st.get(group_items[2].id).fields.barcode == EAN2


def _press(data):
    from types import SimpleNamespace as NS
    from tests.test_group_bot import USER
    m = Msg()

    async def ans(*a, **k):
        pass
    return NS(callback_query=NS(data=data, answer=ans, edit_message_reply_markup=m._noop, message=m),
              effective_user=USER, effective_message=m)


def test_correction_schema_keeps_the_barcode():
    from intake_bot.vision import CorrectionFields
    f = ItemFields(brand="Seiko", barcode=EAN)
    assert CorrectionFields.from_item(f).to_item().barcode == EAN
