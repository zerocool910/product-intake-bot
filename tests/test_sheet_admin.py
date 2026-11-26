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
    assert "Not switched: items are staged" in last()[0] and bot.sheet is sheet and not saved
    run(bot.text(upd(Msg("⏹ End intake")), None))                    # written to the OLD sheet
    assert len(sheet.current().get_all_records()) == 1
    press(bot, f"sh:set:{NEW_ID}")
    assert bot.sheet is new and saved == {"SHEET_ID": NEW_ID}
    assert "Now using New receipts" in last()[0] and "Legend" in new.book.tabs
