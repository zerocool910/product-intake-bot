"""Sheet.save: every confirmation is its own receipt line, even for a repeat SKU."""

from intake_bot.sheet import HEADER, Sheet


class FakeWorksheet:
    def __init__(self, rows):
        self.rows = [list(HEADER)] + [[r.get(c, "") for c in HEADER] for r in rows]

    def get_all_records(self, expected_headers=None):
        return [dict(zip(HEADER, r)) for r in self.rows[1:]]

    def append_row(self, values, value_input_option=None):
        self.rows.append(list(values))


def make(rows):
    s = object.__new__(Sheet)
    s.ws = FakeWorksheet(rows)
    return s


def test_repeat_sku_is_a_new_receipt_line():
    s = make([{"intake_id": "first", "preview_sku": "SRPD53K1", "quantity": 1}])
    s.save({"intake_id": "second", "preview_sku": "SRPD53K1", "quantity": 2})
    recs = s.ws.get_all_records()
    assert [(r["intake_id"], r["quantity"]) for r in recs] == [("first", 1), ("second", 2)]


def test_same_sku_lists_earlier_receipts_with_row_numbers():
    s = make([{"intake_id": "a", "preview_sku": "X1"}, {"intake_id": "b", "preview_sku": "srpd53k1"}])
    assert [(r["intake_id"], r["_row"]) for r in s.same_sku("SRPD53K1")] == [("b", 3)]
