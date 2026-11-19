"""Sheet.save: same SKU overwrites the existing row, new SKU appends."""

from intake_bot.sheet import HEADER, Sheet


class FakeWorksheet:
    def __init__(self, rows):
        self.rows = [list(HEADER)] + [[r.get(c, "") for c in HEADER] for r in rows]

    def get_all_records(self, expected_headers=None):
        return [dict(zip(HEADER, r)) for r in self.rows[1:]]

    def append_row(self, values, value_input_option=None):
        self.rows.append(list(values))

    def update(self, values, range_name, value_input_option=None):
        n = int(range_name.lstrip("A"))
        self.rows[n - 1] = list(values[0])


def make(rows):
    s = object.__new__(Sheet)
    s.ws = FakeWorksheet(rows)
    return s


def test_new_sku_is_appended():
    s = make([{"intake_id": "a", "preview_sku": "SRPD53K1"}])
    assert s.save({"intake_id": "b", "preview_sku": "SNK809K1"}) == []
    assert [r["intake_id"] for r in s.ws.get_all_records()] == ["a", "b"]


def test_same_sku_overwrites_in_place():
    s = make([{"intake_id": "a", "preview_sku": "X1"},
              {"intake_id": "old", "preview_sku": "SRPD53K1", "supplied_price": 1_250_000},
              {"intake_id": "c", "preview_sku": "X2"}])
    replaced = s.save({"intake_id": "new", "preview_sku": "srpd53k1", "supplied_price": 5_000_000})
    assert replaced == ["old"]
    recs = s.ws.get_all_records()
    assert [r["intake_id"] for r in recs] == ["a", "new", "c"]
    assert recs[1]["supplied_price"] == 5_000_000


def test_extra_copies_are_cleared():
    s = make([{"intake_id": "a", "preview_sku": "DUP"}, {"intake_id": "b", "preview_sku": "DUP"}])
    assert s.save({"intake_id": "new", "preview_sku": "DUP"}) == ["a", "b"]
    recs = s.ws.get_all_records()
    assert recs[0]["intake_id"] == "new"
    assert all(v == "" for v in recs[1].values())
