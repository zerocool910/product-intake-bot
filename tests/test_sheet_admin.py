"""/sheet: view, and admin-only switching with .env update."""

import asyncio
from types import SimpleNamespace as NS

import intake_bot.bot as botmod
from intake_bot.config import update_env
from tests.test_group_bot import Msg, USER, last, make_bot, press, run, upd
from tests.test_revoke import seiko
from tests.test_sheet import FakeSheet

NEW_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-xyz"


def sheet_cmd(bot, *args):
    run(bot.sheet_command(upd(Msg("/sheet")), NS(args=list(args))))


def setup(tmp_path, admin=True):
    bot, st, sheet = make_bot(tmp_path, seiko())
    bot.s = NS(**{**vars(bot.s), "admin_user_ids": frozenset({USER.id} if admin else set()),
                  "google_credentials_file": str(tmp_path / "missing.json"), "worksheet": "Intake",
                  "timezone": "Asia/Jakarta"})
    sheet.book.title = "Old receipts"
    return bot, st, sheet


def test_update_env_replaces_or_appends(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=x\nSHEET_ID=old\n# comment\n")
    update_env("SHEET_ID", "new", env)
    update_env("ADMIN_USER_IDS", "42", env)
    assert env.read_text() == "TELEGRAM_BOT_TOKEN=x\nSHEET_ID=new\n# comment\nADMIN_USER_IDS=42\n"


def test_view_and_non_admin_refused(tmp_path):
    bot, st, sheet = setup(tmp_path, admin=False)
    sheet_cmd(bot)
    assert "Spreadsheet: Old receipts" in last()[0] and "FAKE_SHEET_ID" in last()[0]
    sheet_cmd(bot, "set", NEW_ID)
    assert "Only an admin" in last()[0]


def test_unshared_sheet_names_the_account_to_share_with(tmp_path):
    bot, st, sheet = setup(tmp_path)
    (tmp_path / "missing.json").write_text('{"client_email": "intake-bot@proj.iam.gserviceaccount.com"}')
    bot._probe_sheet = lambda sid: (_ for _ in ()).throw(PermissionError("403"))
    sheet_cmd(bot, "set", f"https://docs.google.com/spreadsheets/d/{NEW_ID}/edit#gid=0")
    assert "Share it with intake-bot@proj.iam.gserviceaccount.com as Editor" in last()[0]


def test_switch_blocked_while_items_are_staged_then_done(tmp_path, monkeypatch):
    bot, st, sheet = setup(tmp_path)
    saved = {}
    monkeypatch.setattr(botmod, "update_env", lambda k, v: saved.update({k: v}))
    new = FakeSheet(sheet.day); new.book.id, new.book.title = NEW_ID, "New receipts"
    bot._probe_sheet = lambda sid: "New receipts"
    bot._open_sheet = lambda sid: new
    run(bot.photo(upd(Msg(caption="Seiko 1.250.000 sku SRPD53K1 G", photo=True)), None))
    item = st.latest_pending(1, 2)
    press(bot, f"confirm:{item.id}")                                  # staged in an open session
    sheet_cmd(bot, "set", f"https://docs.google.com/spreadsheets/d/{NEW_ID}/edit")
    assert "Switch the bot to New receipts?" in last()[0] and last()[1] == ["✅ Switch", "Keep"]
    press(bot, f"sh:set:{NEW_ID}")
    assert "Not changed: items are staged" in last()[0] and bot.sheet is sheet and not saved
    run(bot.text(upd(Msg("⏹ End intake")), None))                    # written to the OLD sheet
    assert len(sheet.current().get_all_records()) == 1
    press(bot, f"sh:set:{NEW_ID}")
    assert bot.sheet is new and saved == {"SHEET_ID": NEW_ID}
    assert "Now using New receipts" in last()[0] and "Legend" in new.book.tabs


def test_other_tasks_lists_commands_and_admin_ones_only_for_admins(tmp_path):
    for admin in (False, True):
        bot, st, sheet = setup(tmp_path, admin=admin)
        st.end_session(st.active_session(1, 2).id)                 # menu answers "2" outside a session
        run(bot.text(upd(Msg("2")), None))
        text = last()[0]
        for cmd in ("cek SRPD53K1", "revoke <id>", "/sheet", "📋 Recap", "⏹ End intake", "1,3 1.900.000"):
            assert cmd in text, cmd
        assert ("/sheet set" in text) is admin


def test_fresh_tab_asks_then_renames_and_is_blocked_while_staged(tmp_path):
    bot, st, sheet = setup(tmp_path)
    sheet.append({"intake_id": "x", "preview_sku": "A1", "quantity": 1, "receipt_date": "2026-11-01"})
    run(bot.photo(upd(Msg(caption="Seiko 1.250.000 sku SRPD53K1 G", photo=True)), None))
    press(bot, f"confirm:{st.latest_pending(1, 2).id}")
    sheet_cmd(bot, "fresh", "Intake 2026-11 trial")
    assert "Start an empty Intake" in last()[0] and last()[1] == ["🧹 Start fresh tab", "Keep"]
    press(bot, "sh:fresh:-")
    assert "Not changed: items are staged" in last()[0] and "Intake 2026-11 trial" not in sheet.book.tabs
    run(bot.text(upd(Msg("⏹ End intake")), None))
    sheet_cmd(bot, "fresh", "Intake 2026-11 trial")
    press(bot, "sh:fresh:-")
    assert "Fresh tab" in last()[0] and "Intake 2026-11 trial" in last()[0]
    assert len(sheet.book.tabs["Intake 2026-11 trial"].get_all_records()) == 2
    assert sheet.current().get_all_records() == []


def test_new_file_needs_a_folder_and_explains_the_manual_route(tmp_path, monkeypatch):
    bot, st, sheet = setup(tmp_path)
    sheet_cmd(bot, "new")
    assert "can't create files myself" in last()[0] and "/sheet set" in last()[0]
    bot.s = NS(**{**vars(bot.s), "sheet_folder_id": "FOLDER"})
    saved = {}
    monkeypatch.setattr(botmod, "update_env", lambda k, v: saved.update({k: v}))
    new = FakeSheet(sheet.day); new.book.id, new.book.title = NEW_ID, "Intake 2027"
    bot._create_sheet = lambda name: NEW_ID
    bot._open_sheet = lambda sid: new
    sheet_cmd(bot, "new", "Intake", "2027")
    assert "Create a new spreadsheet Intake 2027" in last()[0]
    press(bot, "sh:new:-")
    assert bot.sheet is new and saved == {"SHEET_ID": NEW_ID} and "Now using Intake 2027" in last()[0]
    bot._create_sheet = lambda name: (_ for _ in ()).throw(PermissionError("quota"))
    sheet_cmd(bot, "new")
    press(bot, "sh:new:-")
    assert "Couldn't create the file (PermissionError)" in last()[0] and bot.sheet is new
