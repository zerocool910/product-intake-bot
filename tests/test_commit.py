"""Owner rule: nothing reaches the sheet until End intake."""

from tests.test_group_bot import Msg, end_intake, last, make_bot, press, run, upd
from tests.test_revoke import rows, seiko


def test_sheet_untouched_until_end_intake(tmp_path):
    bot, st, sheet = make_bot(tmp_path, seiko())
    run(bot.photo(upd(Msg(caption="Seiko 1.250.000 sku SRPD53K1 G", photo=True)), None))
    item = st.latest_pending(1, 2)
    press(bot, f"confirm:{item.id}")
    assert "Written to the sheet when you end intake" in last()[0]
    assert rows(sheet) == [] and st.get(item.id).status == "staged"
    run(bot.text(upd(Msg("⏹ End intake")), None))
    assert rows(sheet) == [(item.id, 1, "")]
    assert any("1 item(s) written to the sheet" in o[0] for o in Msg.out[-3:])


def test_add_qty_onto_an_item_staged_in_the_same_session(tmp_path):
    bot, st, sheet = make_bot(tmp_path, seiko())
    caption = "Seiko 1.250.000 sku SRPD53K1 G"
    run(bot.photo(upd(Msg(caption=caption, photo=True)), None))
    a = st.latest_pending(1, 2)
    run(bot.photo(upd(Msg(caption=caption + " 2pcs", photo=True)), None))
    b = st.latest_pending(1, 2)
    press(bot, f"confirm:{b.id}")                    # the later photo confirmed first
    press(bot, f"confirm:{a.id}")                    # same SKU staged today: plain Confirm is refused
    assert st.get(a.id).status == "pending"
    press(bot, f"addqty:{a.id}")
    assert "will add to today's" in last()[0]
    end_intake(bot, st)
    assert rows(sheet) == [(b.id, 3, f"+1 ({a.id})")]


def test_failed_write_keeps_the_session_open(tmp_path):
    bot, st, sheet = make_bot(tmp_path, seiko())
    run(bot.photo(upd(Msg(caption="Seiko 1.250.000 sku SRPD53K1 G", photo=True)), None))
    item = st.latest_pending(1, 2)
    press(bot, f"confirm:{item.id}")
    real_append = sheet.append
    sheet.append = lambda row: (_ for _ in ()).throw(ConnectionError("sheet down"))
    run(bot.text(upd(Msg("⏹ End intake")), None))
    assert "Couldn't write to the sheet" in last()[0] and st.active_session(1, 2) is not None
    assert st.get(item.id).status == "staged"
    sheet.append = real_append
    run(bot.text(upd(Msg("⏹ End intake")), None))
    assert rows(sheet) == [(item.id, 1, "")] and st.active_session(1, 2) is None
