"""Monthly tabs, receipt dates, same-day Replace / Add qty."""

from datetime import date

import gspread
from gspread.utils import a1_to_rowcol

from intake_bot.sheet import HEADER, Sheet


class FakeWorksheet:
    def __init__(self, title):
        self.title, self.rows, self.col_count = title, [], len(HEADER)
        self.id = abs(hash(title)) % 10**9

    def row_values(self, n):
        return self.rows[n - 1] if len(self.rows) >= n else []

    def add_cols(self, n):
        self.col_count += n

    def update_notes(self, notes):
        pass

    def freeze(self, rows=None, cols=None):
        pass

    def get_all_records(self, expected_headers=None):
        return [dict(zip(HEADER, r)) for r in self.rows[1:]]

    def append_row(self, values, value_input_option=None):
        self.rows.append(list(values))

    def delete_rows(self, index):
        del self.rows[index - 1]

    def update(self, values, range_name, value_input_option=None):
        row, col = a1_to_rowcol(range_name)
        while len(self.rows) < row:
            self.rows.append([""] * len(HEADER))
        if len(values[0]) > 1:
            self.rows[row - 1] = list(values[0])
        else:
            self.rows[row - 1][col - 1] = values[0][0]


class FakeBook:
    def __init__(self):
        self.tabs = {}
        self.id = "FAKE_SHEET_ID"

    def worksheet(self, title):
        if title not in self.tabs:
            raise gspread.WorksheetNotFound(title)
        return self.tabs[title]

    def worksheets(self):
        return list(self.tabs.values())

    def add_worksheet(self, title, rows, cols):
        self.tabs[title] = FakeWorksheet(title)
        return self.tabs[title]


class FakeSheet(Sheet):
    def __init__(self, day):
        self.book, self.prefix, self._tabs, self.day = FakeBook(), "Intake", {}, day

    def today(self):
        return self.day


def receipt(sheet, intake_id, sku, qty=1):
    return {"intake_id": intake_id, "preview_sku": sku, "quantity": qty, "receipt_date": sheet.today().isoformat()}


def test_rows_go_to_this_months_tab_and_a_new_month_starts_a_new_tab():
    s = FakeSheet(date(2026, 11, 30))
    s.append(receipt(s, "a", "SRPD53K1"))
    s.day = date(2026, 12, 1)
    s.append(receipt(s, "b", "SRPD53K1"))
    assert set(s.book.tabs) == {"Intake 2026-11", "Intake 2026-12"}
    assert s.book.tabs["Intake 2026-11"].rows[0] == HEADER
    assert [r["intake_id"] for r in s.book.tabs["Intake 2026-11"].get_all_records()] == ["a"]
    assert [r["intake_id"] for r in s.book.tabs["Intake 2026-12"].get_all_records()] == ["b"]


def test_same_sku_check_is_today_only():
    s = FakeSheet(date(2026, 11, 19))
    s.append(receipt(s, "yesterday", "SRPD53K1"))
    s.current().rows[1][HEADER.index("receipt_date")] = "2026-11-18"
    assert s.same_sku_today("SRPD53K1") == []          # earlier date = new receipt, no question
    s.append(receipt(s, "today", "srpd53k1"))
    [hit] = s.same_sku_today("SRPD53K1")
    assert (hit["intake_id"], hit["_row"]) == ("today", 3)


def test_replace_overwrites_the_row_in_place():
    s = FakeSheet(date(2026, 11, 19))
    s.append(receipt(s, "first", "X1", 1))
    s.append(receipt(s, "other", "X2", 1))
    [hit] = s.same_sku_today("X1")
    s.replace(hit["_row"], receipt(s, "second", "X1", 3))
    assert [(r["intake_id"], r["quantity"]) for r in s.current().get_all_records()] == [("second", 3), ("other", 1)]


def test_add_quantity_adds_to_the_row_and_notes_it():
    s = FakeSheet(date(2026, 11, 19))
    s.append(receipt(s, "first", "X1", 1))
    [hit] = s.same_sku_today("X1")
    assert s.add_quantity(hit, 2, "+2 (second)") == 3
    [row] = s.current().get_all_records()
    assert (row["intake_id"], row["quantity"], row["notes"]) == ("first", 3, "+2 (second)")


def test_name_clash_check_spans_earlier_months():
    from intake_bot.catalog import Catalog
    from intake_bot.item import ItemFields, auto_descriptor, to_import_row
    C = Catalog.load()
    ac = dict(type="watch", brand="Alexandre Christie", ref_code="1007", strap_type="Stainless St.",
              strap_color="Black", case_color="Black", gender="Gents", supplied_price=950_000)
    s = FakeSheet(date(2026, 11, 10))
    s.append({**to_import_row(C, ItemFields(**ac)), "intake_id": "A-white", "photo_dial_color": "White",
              "receipt_date": "2026-11-10"})
    s.book.add_worksheet("Legend", 1, 1)          # not a month tab: ignored
    s.day = date(2026, 12, 3)                      # new month, empty tab
    blue = ItemFields(**ac, photo_dial_color="Blue")
    clashes = s.similar(to_import_row(C, blue))
    assert [c["intake_id"] for c in clashes] == ["A-white"]
    new, text = auto_descriptor(C, blue, clashes)
    assert text == "diBLU"                         # so its SKU differs from model A's
