"""SKU / model lookup ("cek …")."""

import asyncio
from datetime import date
from types import SimpleNamespace as NS

from intake_bot.catalog import Catalog
from intake_bot.lookup import build_prefix, format_result, parse_lookup
from intake_bot.master import MasterUnavailable, Product
from tests.test_group_bot import Msg, USER, make_bot, upd

C = Catalog.load()


def test_parse_and_prefix():
    assert parse_lookup("cek SRPD53K1") == "SRPD53K1"
    assert parse_lookup("/cek") == "" and parse_lookup("Cek ac 5014") == "ac 5014"
    assert parse_lookup("Seiko 1.250.000") is None and parse_lookup("check-in") is None
    assert build_prefix(C, "ac 5014") == ("AC5014", "Alexandre Christie 5014")
    assert build_prefix(C, "Alexandre Christie 2C07") == ("AC2C07", "Alexandre Christie 2C07")
    assert build_prefix(C, "srpd53k1") == ("SRPD53K1", "SRPD53K1")
    assert build_prefix(C, "Seiko SRPD53K1")[0] == "SEIKOSRPD53K1"   # non-AC brand name isn't a prefix


def test_format_found_variants_missing_and_unreachable():
    t = format_result("SRPD53K1", "SRPD53K1", [Product("SRPD53K1", "Seiko SRPD53K1 SS SIL Bd SIL. G - DUMO", 2)],
                      [{"quantity": 1, "receipt_date": "2026-11-20"}, {"quantity": 2, "receipt_date": "2026-11-24"}])
    assert "📦 In accounting: <code>SRPD53K1</code>" in t and "Stock: <b>2</b>" in t
    assert "2 receipt(s), 3 pcs, last on 2026-11-24" in t
    t = format_result("Alexandre Christie 5014", "AC5014",
                      [Product("AC5014SS20C20Ldi11", "", 2), Product("AC5014DT20C18Ldi01", "", 0)], [])
    assert "In accounting <code>AC5014</code> (2):" in t and "AC5014DT20C18Ldi01</code> — stock 0" in t
    assert "Not received via the bot yet" in t
    assert "🆕 Not in accounting yet" in format_result("X", "XYZ1", [], [])
    assert "Accounting not reachable" in format_result("X", "XYZ1", None, None)


def test_cek_from_chat_never_touches_pending_items(tmp_path):
    def by_prefix(p):
        if p == "DOWN1":
            raise MasterUnavailable("timeout")
        return [x for x in [Product("SRPD53K1", "Seiko SRPD53K1 SS SIL Bd SIL. G - DUMO", 2)]
                if x.sku.startswith(p)]
    bot, st, sheet = make_bot(tmp_path, [], master=NS(by_prefix=by_prefix))
    st.create("i1", 1, 2, "Ani", __import__("intake_bot.item", fromlist=["ItemFields"]).ItemFields(brand="Seiko"), [])
    asyncio.run(bot.text(upd(Msg("cek srpd53k1")), None))
    assert "📦 In accounting: SRPD53K1" in Msg.out[-1][0] and "Stock: 2" in Msg.out[-1][0]
    asyncio.run(bot.text(upd(Msg("cek DOWN1")), None))
    assert "Accounting not reachable" in Msg.out[-1][0]
    assert st.get("i1").status == "pending" and st.get("i1").fields.brand == "Seiko"
