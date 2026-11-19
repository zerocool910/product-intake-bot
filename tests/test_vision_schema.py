"""The API rejects structured-output schemas with more than 16 union/nullable
fields. Guard the schemas we send so this can't regress."""

import pytest
from anthropic.lib._parse._transform import transform_schema

from intake_bot.item import ItemFields
from intake_bot.vision import CorrectionFields, PhotoFields

API_UNION_LIMIT = 16


def _unions(schema: dict) -> int:
    n = 0
    for prop in schema.get("properties", {}).values():
        if "anyOf" in prop or isinstance(prop.get("type"), list):
            n += 1
    return n


@pytest.mark.parametrize("model", [PhotoFields, CorrectionFields])
def test_schema_within_union_limit(model):
    assert _unions(transform_schema(model)) <= API_UNION_LIMIT


def test_itemfields_itself_is_over_the_limit():
    # Documents why ItemFields isn't sent directly.
    assert _unions(transform_schema(ItemFields)) > API_UNION_LIMIT


def test_correction_round_trip():
    f = ItemFields(type="watch", brand="Seiko", strap_type="Stainless St.", gender="Gents",
                   supplied_price=1_250_000, quantity=2, uncertain=["strap_type"], notes="dial black")
    back = CorrectionFields.from_item(f).to_item()
    assert back == f
    empty = CorrectionFields.from_item(ItemFields()).to_item()
    assert empty == ItemFields()
