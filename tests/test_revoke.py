"""Revoking saved items: whole group, one watch, Add qty, Replace, warnings."""

import asyncio
from datetime import date

from intake_bot.item import ItemFields
from tests.test_group_bot import FIVE, Msg, last, make_bot, press, run, upd


def rows(sheet):
    return [(r["intake_id"], r["quantity"], r["notes"]) for r in sheet.current().get_all_records()]


def seiko(qty=None):
    return [ItemFields(type="watch", brand="Seiko", strap_type="Stainless St.", strap_color="Silver",
                       case_color="Silver", quantity=qty)]


def save_group(bot, st):
    run(bot.photo(upd(Msg(caption="Ac 5014 2.000.000 L 5 pcs", photo=True)), None))
    gid = st.latest_pending_group(1, 2).id
    press(bot, f"g:{gid}:confirm")
    return gid


def test_revoke_whole_group_asks_first_then_removes_rows(tmp_path):
    bot, st, sheet = make_bot(tmp_path, FIVE)
    gid = save_group(bot, st)
    assert len(rows(sheet)) == 5
    run(bot.text(upd(Msg(f"revoke {gid}")), None))
    text, keys = last()
    assert f"Revoke {gid}?" in text and text.count("line removed") == 5 and keys == ["🗑 Revoke 5", "Keep"]
    assert len(rows(sheet)) == 5                                     # nothing happens before the tap
    press(bot, f"rv:{gid}:go")
    assert rows(sheet) == [] and "Revoked 5 item(s), 5 pcs" in last()[0]
    assert st.group(gid).status == "revoked"
    assert all(i.status == "revoked" for i in st.group_items(gid))
    run(bot.text(upd(Msg(f"revoke {gid}")), None))
    assert "Nothing saved found" in last()[0]                       # can't revoke twice


def test_revoke_one_watch_of_a_group(tmp_path):
    bot, st, sheet = make_bot(tmp_path, FIVE)
    gid = save_group(bot, st)
    run(bot.text(upd(Msg(f"batal {gid}-3")), None))
    press(bot, f"rv:{gid}-3:go")
    assert [r[0] for r in rows(sheet)] == [f"{gid}-{n}" for n in (1, 2, 4, 5)]
    assert st.group(gid).status != "revoked"                        # the rest stays saved


def test_revoke_add_qty_and_replace(tmp_path):
    bot, st, sheet = make_bot(tmp_path, seiko())
    caption = "Seiko 1.250.000 sku SRPD53K1 G"
    for n, action in ((1, "confirm"), (2, "addqty")):
        run(bot.photo(upd(Msg(caption=caption + (" 2pcs" if n == 2 else ""), photo=True)), None))
        press(bot, f"{action}:{st.latest_pending(1, 2).id}")
    first, second = [i for i in st.session_items(st.active_session(1, 2).id)]
    assert rows(sheet) == [(first.id, 3, f"+2 ({second.id})")]
    run(bot.text(upd(Msg(f"revoke {second.id}")), None))
    assert "pcs taken off the line it was added to" in last()[0]
    press(bot, f"rv:{second.id}:go")
    assert rows(sheet) == [(first.id, 1, "")]
    # Replace, then revoke the replacement: the original line comes back.
    run(bot.photo(upd(Msg(caption=caption + " 4pcs", photo=True)), None))
    third = st.latest_pending(1, 2)
    press(bot, f"replace:{third.id}")
    assert rows(sheet) == [(third.id, 4, f"[replaces {first.id}]")]
    run(bot.text(upd(Msg(f"revoke {third.id}")), None))
    assert "the line it replaced is put back" in last()[0]
    press(bot, f"rv:{third.id}:go")
    assert rows(sheet) == [(first.id, 1, "")]
    assert st.get(first.id).status == "confirmed"


def test_older_day_warns_about_accounting_and_unknown_id(tmp_path):
    bot, st, sheet = make_bot(tmp_path, seiko())
    run(bot.photo(upd(Msg(caption="Seiko 1.250.000 sku SRPD53K1 G", photo=True)), None))
    item = st.latest_pending(1, 2)
    press(bot, f"confirm:{item.id}")
    sheet.day = date(2026, 11, 26)
    run(bot.text(upd(Msg(f"revoke {item.id}")), None))
    assert "received on 2026-11-25" in last()[0] and "does NOT remove it there" in last()[0]
    run(bot.text(upd(Msg("revoke 20991231-abcdef")), None))
    assert "Nothing saved found" in last()[0]


def test_revoke_never_touches_other_receipts_in_between(tmp_path):
    bot, st, sheet = make_bot(tmp_path, FIVE)
    gid = save_group(bot, st)
    # Move two other receipts between the group's rows (as if logged in between).
    ws = sheet.current()
    hdr = ws.rows[0]
    other = lambda iid: [iid if h == "intake_id" else ("1" if h == "quantity" else "") for h in hdr]
    ws.rows.insert(2, other("OTHER-A"))
    ws.rows.insert(5, other("OTHER-B"))
    run(bot.text(upd(Msg(f"revoke {gid}")), None))
    press(bot, f"rv:{gid}:go")
    assert [r[0] for r in rows(sheet)] == ["OTHER-A", "OTHER-B"]
