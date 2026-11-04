"""Deterministic parsing of the photo caption.

Staff type something like:  "Seiko 1.250.000 G qty 2"  or  "AC 950k ref AE1200 L"
Anything found here overrides what the vision model proposed.
"""

from __future__ import annotations

import re

from .catalog import Catalog
from .item import ItemFields

_PRICE = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)\s*(jt|juta|k|rb|ribu)?(?![\w.,])",
    re.IGNORECASE,
)
_QTY = re.compile(r"\b(?:qty|jumlah|pcs)\s*[:=]?\s*(\d+)\b|\bx\s?(\d+)\b", re.IGNORECASE)
_REF = re.compile(r"\bref\s*[:=]?\s*([A-Za-z0-9]+)\b", re.IGNORECASE)
_SKU = re.compile(r"\bsku\s*[:=]?\s*([A-Za-z0-9-]+)\b", re.IGNORECASE)
_GENDER = re.compile(r"(?<!\w)(G/L|gents|ladies|men|women|pria|wanita|unisex|G|L)(?!\w)")
_GENDER_MAP = {"g/l": "Gents/Ladies", "gents": "Gents", "men": "Gents", "pria": "Gents", "g": "Gents",
               "ladies": "Ladies", "women": "Ladies", "wanita": "Ladies", "l": "Ladies", "unisex": "All"}
_MULT = {"jt": 1_000_000, "juta": 1_000_000, "k": 1_000, "rb": 1_000, "ribu": 1_000}


def parse_price(text: str) -> float | None:
    """Largest money-looking number in the text, in Rupiah.

    Accepts 1.250.000 / 1,250,000 / 1250000 / 1250k / 1,25jt. Bare numbers
    under 10,000 without a suffix are ignored (they're quantities or
    ref numbers, not prices).
    """
    best = None
    for m in _PRICE.finditer(text):
        raw, suffix = m.group(1), (m.group(2) or "").lower()
        if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", raw):
            value = float(re.sub(r"[.,]", "", raw))
        else:
            value = float(raw.replace(",", "."))
        if suffix:
            value *= _MULT[suffix]
        elif value < 10_000:
            continue
        best = value if best is None or value > best else best
    return best


def parse_caption(catalog: Catalog, text: str | None) -> ItemFields:
    if not text:
        return ItemFields()
    out: dict = {}

    # Strip ref/sku/qty first so their digits aren't read as a price.
    rest = text
    if m := _REF.search(rest):
        out["ref_code"] = m.group(1).upper()
        rest = rest.replace(m.group(0), " ")
    if m := _SKU.search(rest):
        out["manufacturer_sku"] = m.group(1).upper()
        rest = rest.replace(m.group(0), " ")
    if m := _QTY.search(rest):
        out["quantity"] = int(m.group(1) or m.group(2))
        rest = rest.replace(m.group(0), " ")

    if (price := parse_price(rest)) is not None:
        out["supplied_price"] = price

    # Brand: longest name/code/alias that appears as whole words.
    lowered = f" {' '.join(rest.lower().split())} "
    candidates = []
    for b in catalog.brands.items:
        for key in (b.name, b.code, *b.aliases):
            if f" {key.lower()} " in lowered:
                candidates.append((len(key), b))
    if candidates:
        brand = max(candidates, key=lambda c: c[0])[1]
        out["brand"] = brand.name
        if brand.kind == "clock":
            out["type"] = "clock"

    if m := _GENDER.search(rest):
        token = m.group(1)
        # Single letters only count when typed upper-case, to avoid matching words.
        if len(token) > 1 or token.isupper():
            out["gender"] = _GENDER_MAP[token.lower()]

    return ItemFields(**out)


def apply_sale_price(catalog: Catalog, fields: ItemFields) -> ItemFields:
    """For SALE brands the typed number is the selling price, not supplied_price."""
    brand = catalog.brands.get(fields.brand)
    if brand and brand.scheme == "sale" and fields.supplied_price and not fields.selling_price:
        data = fields.model_dump()
        data["selling_price"], data["supplied_price"] = data["supplied_price"], None
        return ItemFields(**data)
    return fields
