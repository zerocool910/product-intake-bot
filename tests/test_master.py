"""Picking the right AC/EXP/CA reference from the product master."""

from intake_bot.catalog import Catalog
from intake_bot.item import ItemFields, review
from intake_bot.master import Product, base_reference, match, parse_descriptor

C = Catalog.load()
AC = dict(type="watch", brand="Alexandre Christie", ref_code="1007", strap_type="Stainless St.",
          strap_color="Black", case_color="Black", gender="Gents", supplied_price=950_000)
BASE = "AC1007SS02C02G"


def item(**kw):
    return ItemFields(**{**AC, **kw})


def test_base_reference_only_for_generated_sku_watches():
    assert base_reference(C, item()) == BASE
    assert base_reference(C, item(dial_color="Blue")) == BASE          # base ignores descriptor
    assert base_reference(C, ItemFields(brand="Seiko", manufacturer_sku="X")) is None
    assert base_reference(C, item(type="clock")) is None


def test_parse_descriptor():
    blue, num, silver = C.colors.get("Blue"), C.index_types.get("Numeral"), C.colors.get("Silver")
    assert parse_descriptor(C, "") == (None, None, None)
    assert parse_descriptor(C, "di04") == (blue, None, None)
    assert parse_descriptor(C, "di04idxNUMcr18") == (blue, num, silver)
    assert parse_descriptor(C, "X") is None


def test_not_in_master_is_new():
    m = match(C, item(), [], BASE)
    assert (m.kind, m.sku) == ("new", BASE)


def test_only_base_in_master_is_restock():
    m = match(C, item(photo_dial_color="White"), [Product(BASE, stock_quantity=3)], BASE)
    assert (m.kind, m.sku) == ("restock", BASE)
    assert "stock 3" in m.note


def test_seen_dial_picks_the_matching_variant():
    master = [Product(BASE), Product(BASE + "di04"), Product(BASE + "di12")]
    m = match(C, item(photo_dial_color="Blue"), master, BASE)
    assert (m.kind, m.sku, m.fields.dial_color) == ("restock", BASE + "di04", "Blue")
    assert review(C, m.fields).sku == BASE + "di04"                       # the row's reference matches


def test_unknown_dial_with_variants_asks_staff():
    master = [Product(BASE), Product(BASE + "di04")]
    m = match(C, item(photo_dial_color="Green"), master, BASE)
    assert m.kind == "choose" and BASE in m.options and BASE + "di04" in m.options


def test_no_base_and_new_dial_makes_new_described_product():
    m = match(C, item(photo_dial_color="Green"), [Product(BASE + "di04")], BASE)
    assert (m.kind, m.sku, m.fields.dial_color) == ("new", BASE + "di07", "Green")


def test_staff_descriptor_is_used_as_given():
    master = [Product(BASE), Product(BASE + "di04")]
    assert match(C, item(dial_color="Blue"), master, BASE).sku == BASE + "di04"
    m = match(C, item(dial_color="Red"), master, BASE)
    assert (m.kind, m.sku) == ("new", BASE + "di03")


def test_other_prefix_matches_are_ignored():
    # AC1007SS02C02GX is not a descriptor of AC1007SS02C02G.
    m = match(C, item(), [Product(BASE + "X")], BASE)
    assert (m.kind, m.sku) == ("new", BASE)
