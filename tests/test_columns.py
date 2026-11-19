"""Quantity default, cost price, Claude's dial/index, auto-descriptor, legend."""

from intake_bot.catalog import Catalog
from intake_bot.item import (AUDIT_COLUMNS, IMPORT_COLUMNS, LEGEND, ItemFields, auto_descriptor, review,
                             sample_rows, to_import_row)
from intake_bot.sheet import HEADER, Sheet
from intake_bot.vision import CorrectionFields

C = Catalog.load()
AC = dict(type="watch", brand="Alexandre Christie", ref_code="1007", strap_type="Stainless St.",
          strap_color="Black", case_color="Black", gender="Gents", supplied_price=950_000)


def test_quantity_defaults_to_one_but_typed_wins():
    assert to_import_row(C, ItemFields(**AC))["quantity"] == 1
    assert to_import_row(C, ItemFields(**AC, quantity=3))["quantity"] == 3


def test_cost_price_is_hbb_for_list_and_nett_typed_for_sale():
    assert to_import_row(C, ItemFields(**AC))["cost_price"] == 453_150
    nett = dict(AC, brand="Bonia", ref_code=None, manufacturer_sku="B1", supplied_price=400_000)
    assert to_import_row(C, ItemFields(**nett))["cost_price"] == 400_000
    sale = dict(AC, brand="Police", ref_code=None, manufacturer_sku="P1", supplied_price=None, selling_price=650_000)
    assert to_import_row(C, ItemFields(**sale))["cost_price"] == ""
    assert to_import_row(C, ItemFields(**sale, cost_price=300_000))["cost_price"] == 300_000


def test_seen_dial_and_index_are_not_import_columns():
    row = to_import_row(C, ItemFields(**AC, photo_dial_color="Blue", photo_index_type="Baton"))
    assert row["dial_color"] == "" and row["index_type"] == ""
    assert review(C, ItemFields(**AC, photo_dial_color="Blue")).name == "AC 1007 SS BLK Bd BLK. G - EMO"


def test_correction_keeps_seen_dial_and_index():
    f = ItemFields(**AC, photo_dial_color="Blue", photo_index_type="Baton")
    assert CorrectionFields.from_item(f).to_item() == f


def test_auto_descriptor_dial_then_index():
    f = ItemFields(**AC, photo_dial_color="Blue", photo_index_type="Baton")
    new, text = auto_descriptor(C, f, [{"intake_id": "x", "photo_dial_color": "White"}])
    assert text == "diBLU" and new.dial_color == "Blue" and new.index_type is None
    assert review(C, new).name == "AC 1007 SS BLK Bd BLK diBLU. G - EMO"
    new, text = auto_descriptor(C, f, [{"intake_id": "x", "photo_dial_color": "Blue", "photo_index_type": "Numeral"}])
    assert text == "diBLU-idxBTN" and new.index_type == "Baton"


def test_auto_descriptor_holds_back_when_it_should():
    f = ItemFields(**AC, photo_dial_color="Blue", photo_index_type="Baton")
    same = [{"photo_dial_color": "Blue", "photo_index_type": "Baton"}]
    assert auto_descriptor(C, f, same) is None                       # looks like the same model
    assert auto_descriptor(C, ItemFields(**AC), [{"x": 1}]) is None  # nothing seen
    seiko = ItemFields(**dict(AC, brand="Seiko", ref_code=None, manufacturer_sku="S1"), photo_dial_color="Blue")
    assert auto_descriptor(C, seiko, [{"x": 1}]) is None             # same SKU = replace, not descriptor


def test_legend_covers_every_column_and_samples_are_valid():
    assert set(LEGEND) == set(IMPORT_COLUMNS + AUDIT_COLUMNS)
    samples = sample_rows(C)
    assert all(row["preview_name"] for _, row in samples)
    names = [row["preview_name"] for _, row in samples]
    assert "Seiko SRPD53K1 SS SIL Bd SIL. G - DUMO" in names
    assert "AC 1007 SS BLK Bd BLK diBLU. G - EMO" in names
    assert "Seiko JD QXA756W Bd WHT - UMO" in names


class FakeWS:
    def __init__(self, header, cols):
        self.cells = {1: list(header)}; self.col_count = cols; self.title = "Intake"; self.notes = {}
    def add_cols(self, n): self.col_count += n
    def row_values(self, n): return self.cells.get(n, [])
    def update(self, values, range_name, value_input_option=None): self.cells[int(range_name[1:])] = list(values[0])


def test_old_header_is_extended_in_place():
    old = HEADER[: HEADER.index("notes") + 1]
    ws = FakeWS(old, len(old))
    Sheet._ensure_header(ws)
    assert ws.cells[1] == HEADER and ws.col_count >= len(HEADER)
