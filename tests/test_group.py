"""Multi-item photos: caption total, commands, count check, descriptors, gender."""

from intake_bot.caption import parse_caption
from intake_bot.catalog import Catalog
from intake_bot.group import (count_check, descriptors_within_group, gender_from_codes, parse_command,
                              quick_change, split_caption)
from intake_bot.item import ItemFields, review

C = Catalog.load()


def ac5014(strap, strap_color, case, dial):
    return ItemFields(type="watch", brand="Alexandre Christie", ref_code="5014", strap_type=strap,
                      strap_color=strap_color, case_color=case, gender="Ladies", supplied_price=2_000_000,
                      photo_dial_color=dial)


PHOTO = [ac5014("Stainless St.", "Rose Gold", "Rose Gold", "Rose Gold"),
         ac5014("Stainless St.", "Rose Gold", "Rose Gold", "Brown"),
         ac5014("Rantai Kombi", "Rose Gold", "Silver", "Brown"),
         ac5014("Rantai Kombi", "Rose Gold", "Silver", "White"),
         ac5014("Rantai Kombi", "Rose Gold", "Silver", "Rose Gold")]


def test_staff_caption_applies_to_all_and_total_is_a_checker():
    cap = parse_caption(C, "Ac 5014\nSemua warna 2.000.000\n5 pcs")
    fields, total = split_caption(cap)
    assert total == 5 and fields.quantity is None
    assert (fields.brand, fields.supplied_price) == ("Alexandre Christie", 2_000_000)


def test_ac_ref_from_caption_without_ref_keyword():
    # "Ac 5014": the number after an AC/EXP/CA brand is its ref code.
    assert parse_caption(C, "Ac 5014 semua warna 2.000.000").ref_code == "5014"
    assert parse_caption(C, "Seiko 5.000.000 sku SRPD53K1").ref_code is None


def test_descriptors_tell_the_five_apart():
    items, clashes = descriptors_within_group(C, PHOTO)
    skus = [review(C, f).sku for f in items]
    assert clashes == {}
    assert len(set(skus)) == 5
    assert skus == ["AC5014SS20C20Ldi20", "AC5014SS20C20Ldi11", "AC5014DT20C18Ldi11",
                    "AC5014DT20C18Ldi01", "AC5014DT20C18Ldi20"]
    assert review(C, items[2]).name == "AC 5014 DT RSG Bd SIL diBRN. L - UOOO"


def test_identical_watches_are_reported_not_guessed():
    items, clashes = descriptors_within_group(C, [PHOTO[0], PHOTO[0]])
    assert clashes == {1: 0}


def test_a_single_variant_gets_no_descriptor():
    items, _ = descriptors_within_group(C, [PHOTO[0], PHOTO[2]])
    assert [review(C, f).sku for f in items] == ["AC5014SS20C20L", "AC5014DT20C18L"]


def test_count_check():
    one_each = [ItemFields()] * 5
    assert count_check(5, True, 5, False, one_each)[0]
    ok, line = count_check(5, True, 6, False, one_each)
    assert not ok and "6 pcs" in line
    assert count_check(5, True, 6, False, one_each[:4] + [ItemFields(quantity=2)])[0]   # 4 + 2 = 6
    assert not count_check(5, False, None, False, one_each)[0]                          # unsure, no checker
    assert count_check(5, False, None, True, one_each)[0]                               # staff confirmed


def test_commands():
    assert parse_command("3 dial black") == ("item", 3, "dial black")
    assert parse_command("semua L") == ("all", None, "L")
    assert parse_command("hapus 5") == ("remove", 5, "")
    assert parse_command("1,3 1.900.000") == ("items", [1, 3], "1.900.000")
    assert parse_command("1 + 3 L") == ("items", [1, 3], "L")
    assert parse_command("L") == ("all", None, "L")
    assert parse_command("rubber not leather") is None
    assert quick_change("L") == {"gender": "Ladies"}
    assert quick_change("qty 3") == {"quantity": 3} and quick_change("2 pcs") == {"quantity": 2}
    assert quick_change("dial black") is None


def test_gender_from_existing_codes():
    assert gender_from_codes("AC5014", ["AC5014SS20C20Ldi20", "AC5014DT20C18L"]) == "Ladies"
    assert gender_from_codes("AC5014", ["AC5014SS20C20L", "AC5014SS20C20G"]) is None
    assert gender_from_codes("AC5014", ["AC50141SS20C20G"]) is None     # different model
    assert gender_from_codes("AC5014", []) is None


def test_ac_ref_token_is_not_confused_with_prices():
    f = parse_caption(C, "AC 950k ref 1007 L")
    assert (f.ref_code, f.supplied_price) == ("1007", 950_000)        # explicit ref still wins
    f = parse_caption(C, "Ac 2C07 1.850.000 L")
    assert (f.ref_code, f.supplied_price) == ("2C07", 1_850_000)
    f = parse_caption(C, "Expedition 6800 1.200.000")
    assert (f.brand, f.ref_code) == ("Expedition", "6800")
    f = parse_caption(C, "AC 1.250.000")                               # no model token
    assert (f.ref_code, f.supplied_price) == (None, 1_250_000)



def test_quick_change_price_gender_qty():
    assert quick_change("1.180.000 L") == {"supplied_price": 1_180_000, "gender": "Ladies"}
    assert quick_change("980k G 2pcs") == {"supplied_price": 980_000, "gender": "Gents", "quantity": 2}
    assert quick_change("1.180.000 rubber") is None          # anything else goes to Claude
