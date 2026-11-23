"""Intake sessions: store, database upgrade, end-of-session summary."""

import sqlite3
from zoneinfo import ZoneInfo

from intake_bot.item import ItemFields
from intake_bot.session import greeting, summary
from intake_bot.store import Store

TZ = ZoneInfo("Asia/Jakarta")


def test_existing_database_is_upgraded(tmp_path):
    db = tmp_path / "old.sqlite3"
    con = sqlite3.connect(db)
    con.executescript("""CREATE TABLE items (id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        user_name TEXT NOT NULL, status TEXT NOT NULL, fields TEXT NOT NULL, photos TEXT NOT NULL,
        message_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);""")
    con.execute("INSERT INTO items VALUES ('old1',1,2,'S','pending','{}','[]',NULL,'2026-11-19T01:00:00+00:00','x')")
    con.commit(); con.close()
    s = Store(db)
    old = s.get("old1")
    assert old.status == "pending" and old.session_id is None
    new = s.create("new1", 1, 2, "S", ItemFields(brand="Seiko"), [], session_id=7)
    assert new.session_id == 7


def test_one_open_session_per_user(tmp_path):
    s = Store(tmp_path / "x.sqlite3")
    a = s.start_session(1, 2, "Staff")
    assert s.start_session(1, 2, "Staff").id == a.id      # reuses the open one
    assert s.active_session(1, 3) is None                 # other user: none
    s.end_session(a.id)
    assert s.active_session(1, 2) is None
    assert s.start_session(1, 2, "Staff").id != a.id


def test_summary_counts_saved_items_only(tmp_path):
    s = Store(tmp_path / "x.sqlite3")
    sess = s.start_session(1, 2, "Ani")
    def item(iid, **saved):
        s.create(iid, 1, 2, "Ani", ItemFields(brand="Seiko"), [], session_id=sess.id)
        if saved:
            outcome = saved.pop("outcome", None)
            if outcome:
                s.set_outcome(iid, outcome)
            s.record_saved(iid, **saved)
    item("a", status="confirmed", action="added", qty=2, name="Seiko SRPD53K1 SS SIL Bd SIL. G - DUMO",
         sku="SRPD53K1", hbb=831_775, hjb=1_250_000, outcome="restock")
    item("b", status="merged", action="merged", qty=1, name="Seiko SRPD53K1 SS SIL Bd SIL. G - DUMO",
         sku="SRPD53K1", hbb=831_775, hjb=1_250_000, outcome="restock")
    item("c", status="confirmed", action="added", qty=1, name="Police PL1 LS BRN Bd GLD. G - BMO",
         sku="PL1", hbb=None, hjb=650_000, outcome="new")
    item("d")                                            # still pending
    s.update("d", status="cancelled")
    text = summary(s.end_session(sess.id), s.session_items(sess.id), TZ, "Intake 2026-11")
    assert "<b>3</b> item(s), <b>4</b> pcs" in text
    assert "🆕 1 new · 📦 2 restock" in text
    assert "Rp2,495,325" in text                         # 831,775 × 3 (Police has no cost)
    assert "Rp4,400,000" in text                         # 1,250,000 × 3 + 650,000
    assert "1 SALE item(s) without a cost" in text
    assert "➕ added to an earlier line" in text
    assert "Cancelled: 1" in text and "Intake 2026-11" in text


def test_empty_session_and_greeting(tmp_path):
    s = Store(tmp_path / "x.sqlite3")
    sess = s.start_session(1, 2, "Ani")
    assert "Nothing was saved" in summary(s.end_session(sess.id), [], TZ, "Intake 2026-11")
    assert "1. 📦 <b>Intake</b>" in greeting("Ani") and "Hi Ani!" in greeting("Ani")


def test_question_mark_reply_explains_and_never_edits(tmp_path):
    import asyncio
    from types import SimpleNamespace as NS
    from intake_bot.bot import IntakeBot
    from intake_bot.catalog import Catalog
    sent = []
    class Vision:
        def correct(self, *a):
            raise AssertionError("'?' must not be sent to Claude as a correction")
    class Msg:
        chat_id, reply_to_message = 1, None
        def __init__(self, text): self.text, self.from_user = text, NS(id=2, first_name="A", full_name="A")
        async def reply_text(self, text, **kw): sent.append(text)
    s = Store(tmp_path / "x.sqlite3")
    s.create("i1", 1, 2, "A", ItemFields(brand="Seiko", case_color="Silver", uncertain=["case_color"]), [])
    bot = IntakeBot(NS(allowed_user_ids={2}, data_dir=tmp_path, timezone="Asia/Jakarta"), Catalog.load(), s,
                    Vision(), None)
    for t in ("?", "??", "👍", "help"):
        asyncio.run(bot.text(NS(effective_message=Msg(t), effective_user=Msg(t).from_user), None))
    assert len(sent) == 4 and all("What the marks mean" in x for x in sent)
    assert s.get("i1").fields.uncertain == ["case_color"]   # unchanged
