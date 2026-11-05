"""Checks the deterministic rules against the verified examples in docs/."""

import pytest

from intake_bot.caption import apply_sale_price, parse_caption, parse_price
from intake_bot.catalog import Catalog
from intake_bot.item import IMPORT_COLUMNS, ItemFields, review, to_import_row
from intake_bot.naming import (NamingError, decode_cipher, descriptor_for_name, encode_cipher,
                               generated_sku)
from intake_bot.pricing import price_breakdown

C = Catalog.load()


# ---------- cipher ----------

@pytest.mark.parametrize("hjb,letters", [
    (966_000, "EBB"), (1_480_000, "DACO"), (700_000, "SOO"), (950_000, "EMO"), (1_000_000, "DOOO"),
])
def test_cipher_matches_tags(hjb, letters):
    assert encode_cipher(C, hjb) == letters
    assert decode_cipher(C, letters) == hjb


def test_cipher_truncates_not_rounds():
    assert encode_cipher(C, 966_999) == "EBB"


# ---------- pricing ----------

def test_list_scheme_verified_against_accurate():
    # AC 1007LS BLACK: HJB 950,000, DB 47%+10% -> Harga Beli 453,150 in Accurate.
    p = price_breakdown(C.brands.get("AC"), 950_000)
    assert p.hbb == pytest.approx(453_150)
    assert p.hjb == 950_000


def test_list_discounts_are_sequential_not_additive():
    p = price_breakdown(C.brands.get("Seiko"), 1_000_000)
    assert p.hbb == pytest.approx(1_000_000 * 0.70 * 0.97 * 0.98)


def test_nett_scheme_marks_up_cost():
    p = price_breakdown(C.brands.get("Bonia"), 400_000)
    assert (p.hbb, p.hjb) == (400_000, 600_000)


def test_sale_brand_uses_typed_selling_price():
    assert price_breakdown(C.brands.get("Police"), None, 500_000).hjb == 500_000
    assert price_breakdown(C.brands.get("Police"), 500_000) is None


def test_seiko_jd_and_alba_share_seiko_watch_discounts():
    seiko = C.brands.get("Seiko").purchase_discounts
    assert C.brands.get("Seiko JD").purchase_discounts == seiko
    assert C.brands.get("Alba").purchase_discounts == seiko


# ---------- names / SKU ----------

def _watch(**kw):
    base = dict(brand="Alexandre Christie", strap_type="Stainless St.", strap_color="Silver",
                case_color="Silver", gender="Gents", ref_code="AE1200", supplied_price=950_000)
    base.update(kw)
    return ItemFields(**base)


def test_generated_sku_matches_verified_example():
    r = review(C, _watch())
    assert r.ok, r.errors
    assert r.sku == "ACAE1200SS18C18G"


def test_descriptor_in_name_and_sku():
    r = review(C, _watch(dial_color="Blue", index_type="Numeral", crown_color="Silver"))
    assert r.name == "AC SS SIL Bd SIL diBLU-idxNUM-crSIL. G - EMO"
    assert r.sku == "ACAE1200SS18C18Gdi04idxNUMcr18"


def test_manufacturer_sku_brand_name():
    f = ItemFields(brand="Seiko", strap_type="Stainless St.", strap_color="Black", case_color="Silver",
                   gender="G", manufacturer_sku="SRPD53K1", supplied_price=1_000_000)
    r = review(C, f)
    assert r.ok, r.errors
    assert r.name == "Seiko SS BLK Bd SIL. G - DOOO"
    assert r.sku == "SRPD53K1"


def test_clock_name_has_no_strap_or_gender():
    f = ItemFields(brand="Seiko JD", case_color="Gold", manufacturer_sku="QXA001", supplied_price=480_000)
    r = review(C, f)
    assert r.ok, r.errors
    # Mirrors the accounting app as of 830ee94: Seiko JD prints its full
    # name, and the clock pattern then adds "JD" again. Reported upstream.
    assert r.name == "Seiko JD JD Bd GLD - ACO"


def test_descriptor_must_be_cumulative():
    with pytest.raises(NamingError):
        descriptor_for_name(None, C.index_types.get("Baton"), None)
    r = review(C, _watch(index_type="Baton"))
    assert not r.ok


# ---------- required fields ----------

def test_missing_fields_are_listed():
    r = review(C, ItemFields(brand="Casio", type="watch"))
    joined = " | ".join(r.errors)
    for field in ("strap_type", "strap_color", "case_color", "gender", "manufacturer_sku", "supplied_price"):
        assert field in joined


def test_generated_sku_brand_needs_ref_code_not_mfr_sku():
    r = review(C, _watch(ref_code=None))
    assert any("ref_code" in e for e in r.errors)
    assert not any("manufacturer_sku" in e for e in r.errors)


def test_unknown_values_rejected():
    r = review(C, _watch(strap_color="Mauve"))
    assert any("Mauve" in e for e in r.errors)


def test_import_row_uses_canonical_names_and_template_columns():
    row = to_import_row(C, _watch(brand="ac", strap_type="ss", strap_color="sil", gender="gents"))
    assert list(row) == IMPORT_COLUMNS
    assert row["brand"] == "Alexandre Christie"
    assert row["strap_type"] == "Stainless St."
    assert row["strap_color"] == "Silver"
    assert row["gender"] == "G"
    assert row["supplied_price"] == 950_000


# ---------- caption ----------

@pytest.mark.parametrize("text,value", [
    ("1.250.000", 1_250_000), ("1,250,000", 1_250_000), ("1250000", 1_250_000),
    ("950k", 950_000), ("950rb", 950_000), ("1,25jt", 1_250_000), ("2 pcs", None),
])
def test_parse_price(text, value):
    assert parse_price(text) == value


def test_caption_full():
    f = parse_caption(C, "AC 950k ref AE1200 L qty 2")
    assert (f.brand, f.supplied_price, f.ref_code, f.gender, f.quantity) == (
        "Alexandre Christie", 950_000, "AE1200", "Ladies", 2)


def test_caption_prefers_longest_brand_match():
    f = parse_caption(C, "seiko jd 480.000")
    assert f.brand == "Seiko JD" and f.type == "clock"


def test_caption_lowercase_single_letters_are_not_gender():
    assert parse_caption(C, "Casio 500k l").gender is None


def test_caption_overrides_vision():
    vision = ItemFields(brand="Casio", strap_type="Leather", uncertain=["brand", "strap_type"])
    merged = vision.merged(parse_caption(C, "Seiko 1.000.000"))
    assert merged.brand == "Seiko"
    assert merged.strap_type == "Leather"
    assert merged.uncertain == ["strap_type"]


def test_brand_without_code_prints_full_name():
    f = ItemFields(brand="Police", strap_type="Leather", strap_color="Brown", case_color="Gold",
                   gender="G", manufacturer_sku="PL15000", selling_price=650_000)
    assert review(C, f).name == "Police LS BRN Bd GLD. G - BMO"


def test_print_code_on_tag():
    assert C.brands.get("Casio").printed == "Casio"
    assert C.brands.get("Alexandre Christie").printed == "AC"
    assert C.brands.get("Bonia").printed == "BN"


def test_generated_sku_brands_always_print_their_code():
    # Their SKU starts with the printed code, so the flag must stay on.
    for b in C.brands.items:
        if b.uses_generated_sku:
            assert b.code and b.print_code_on_tag, b.name


def test_caption_smartwatch_lines():
    assert parse_caption(C, "smartwatch 300k").brand == "Smartwatch (non-Huawei)"
    assert parse_caption(C, "huawei smartwatch 300k").brand == "Smartwatch (Huawei)"


def test_sale_brand_number_becomes_selling_price():
    f = apply_sale_price(C, parse_caption(C, "Police 650.000"))
    assert (f.selling_price, f.supplied_price) == (650_000, None)
