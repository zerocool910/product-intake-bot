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
    gid = sheet.current().id
    assert any(f"Written to the sheet tab Intake 2026-11" in o[0] for o in Msg.out[-3:])
    assert sheet.tab_url() == f"https://docs.google.com/spreadsheets/d/FAKE_SHEET_ID/edit#gid={gid}"


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


def test_recap_lists_staged_and_unconfirmed(tmp_path):
    bot, st, sheet = make_bot(tmp_path, seiko())
    run(bot.text(upd(Msg("📋 Recap")), None))
    assert "Nothing confirmed in this session yet" in last()[0]
    caption = "Seiko 1.250.000 sku SRPD53K1 G"
    run(bot.photo(upd(Msg(caption=caption + " 2pcs", photo=True)), None))
    a = st.latest_pending(1, 2)
    press(bot, f"confirm:{a.id}")
    run(bot.photo(upd(Msg(caption=caption, photo=True)), None))
    b = st.latest_pending(1, 2)
    press(bot, f"addqty:{b.id}")
    run(bot.photo(upd(Msg(caption="Seiko 900.000 sku SNK809K1 G", photo=True)), None))
    c = st.latest_pending(1, 2)                                   # not confirmed
    run(bot.text(upd(Msg("rekap")), None))
    text = last()[0]
    assert "Seiko SRPD53K1 SS SIL Bd SIL. G - DUMO · SRPD53K1 — 2 pcs" in text
    assert "1 pcs ➕ adds to today's line" in text and f"id {a.id}" in text
    assert "Staged — NOT in the sheet yet" in text and "Already in the sheet" not in text
    assert "2 item(s), 3 pcs · HBB Rp2,495,325 · HJB Rp3,750,000" in text
    assert f"Not confirmed yet (1): {c.id}" in text
    assert rows(sheet) == []                                      # recap never writes
    sid = st.active_session(1, 2).id
    run(bot.text(upd(Msg("⏹ End intake")), None))                # 1 unconfirmed: asks first
    assert "not confirmed yet" in last()[0]
    press(bot, f"end:discard:{sid}")
    assert rows(sheet) == [(a.id, 3, f"+1 ({b.id})")]
    st.start_session(1, 2, "Ani")
    run(bot.text(upd(Msg("rekap")), None))
    assert "Nothing confirmed in this session yet" in last()[0]


def test_recap_shows_items_already_in_the_sheet(tmp_path):
    # Real chat: groups confirmed before the "write at End intake" update were
    # written at once; the recap only listed staged items, so they looked revoked.
    bot, st, sheet = make_bot(tmp_path, seiko())
    sid = st.active_session(1, 2).id
    run(bot.photo(upd(Msg(caption="Seiko 1.250.000 sku SRPD53K1 G", photo=True)), None))
    old = st.latest_pending(1, 2)
    st.record_saved(old.id, status="confirmed", action="added", qty=1, name="Seiko SRPD53K1 SS SIL Bd SIL. G - DUMO",
                    sku="SRPD53K1", hbb=831_775, hjb=1_250_000)          # as the old version saved it
    run(bot.photo(upd(Msg(caption="Seiko 900.000 sku SNK809K1 G", photo=True)), None))
    new = st.latest_pending(1, 2)
    press(bot, f"confirm:{new.id}")
    run(bot.text(upd(Msg("rekap")), None))
    text = last()[0]
    staged_part, sheet_part = text.split("Already in the sheet")
    assert "SNK809K1" in staged_part and "SRPD53K1" in sheet_part and f"id {old.id}" in sheet_part
