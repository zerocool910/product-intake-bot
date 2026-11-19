"""Product master lookups: which existing product (reference) an item is.

The accounting importer matches each row's reference against the product
master: found -> restock, not found -> new product. The reference is the
manufacturer SKU, except for AC/EXP/CA watches, where it's the built code
(AC1007SS02C02G) plus a Unique Descriptor when one is needed
(AC1007SS02C02Gdi04). The descriptor comes from the photo, which the
importer never sees, so the bot must pick the right reference here, before
import, by looking at the product master through the accounting app's
read-only API.

API contract (accounting side):
    GET {ACCOUNTING_API_URL}/api/intake/products?sku_prefix=AC1007SS02C02G
    Authorization: Bearer {ACCOUNTING_API_TOKEN}
    200 -> {"products": [{"sku": "...", "name": "...", "stock_quantity": 3}, ...]}
    Products whose sku starts with sku_prefix, case-insensitive.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import httpx

from .catalog import Catalog, Code
from .item import ItemFields
from .naming import descriptor_for_sku, generated_sku

log = logging.getLogger(__name__)


class MasterUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Product:
    sku: str
    name: str = ""
    stock_quantity: float | None = None


class ProductMaster:
    def __init__(self, base_url: str, token: str, timeout: float = 8.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def by_prefix(self, prefix: str) -> list[Product]:
        try:
            r = httpx.get(f"{self.base_url}/api/intake/products", params={"sku_prefix": prefix},
                          headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
                          timeout=self.timeout)
            r.raise_for_status()
            return [Product(sku=str(p["sku"]), name=str(p.get("name") or ""), stock_quantity=p.get("stock_quantity"))
                    for p in r.json().get("products", [])]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            # Never log the token; httpx errors carry the URL only.
            raise MasterUnavailable(f"product master lookup failed: {e}") from e


@dataclass
class Match:
    """Outcome of matching an item against the product master."""

    kind: str                      # "restock" | "new" | "choose"
    fields: ItemFields             # possibly with dial/index/crown set to the matched product's
    sku: str | None = None
    note: str = ""
    options: list[str] = field(default_factory=list)  # for "choose"


_DESC = re.compile(r"^DI(\d{2})(?:IDX([A-Z]+?))?(?:CR(\d{2}))?$")


def parse_descriptor(catalog: Catalog, suffix: str) -> tuple[Code | None, Code | None, Code | None] | None:
    """'di04idxNUMcr18' -> (Blue, Numeral, Silver); '' -> (None, None, None);
    None if it isn't a descriptor."""
    if not suffix:
        return None, None, None
    m = _DESC.match(suffix.upper())
    if not m:
        return None
    by_number = {c.number: c for c in catalog.colors.items}
    dial = by_number.get(m.group(1))
    index = catalog.index_types.get(m.group(2)) if m.group(2) else None
    crown = by_number.get(m.group(3)) if m.group(3) else None
    if not dial or (m.group(2) and not index) or (m.group(3) and not crown):
        return None
    return dial, index, crown


def _with_descriptor(f: ItemFields, dial: Code | None, index: Code | None, crown: Code | None) -> ItemFields:
    data = f.model_dump()
    data.update(dial_color=dial.name if dial else None, index_type=index.name if index else None,
                crown_color=crown.name if crown else None)
    return ItemFields(**data)


def match(catalog: Catalog, f: ItemFields, products: list[Product], base: str) -> Match:
    """Decide the reference for an AC/EXP/CA watch whose built code without
    descriptor is `base`, given the master products starting with `base`."""
    def stock(p: Product) -> str:
        return f", stock {p.stock_quantity:g}" if isinstance(p.stock_quantity, (int, float)) else ""

    variants: list[tuple[Product, Code | None, Code | None, Code | None]] = []
    for p in products:
        if not p.sku.upper().startswith(base.upper()):
            continue
        parsed = parse_descriptor(catalog, p.sku[len(base):])
        if parsed is not None:
            variants.append((p, *parsed))
    base_product = next((p for p, d, _, _ in variants if d is None), None)
    described = [v for v in variants if v[1] is not None]

    # Staff gave a descriptor explicitly ("dial blue"): use exactly that.
    if f.dial_color:
        dial, index, crown = (catalog.colors.get(f.dial_color), catalog.index_types.get(f.index_type),
                              catalog.colors.get(f.crown_color))
        sku = base + descriptor_for_sku(dial, index, crown)
        hit = next((p for p, *_ in variants if p.sku.upper() == sku.upper()), None)
        if hit:
            return Match("restock", f, hit.sku, f"📦 Restock of existing <code>{hit.sku}</code>{stock(hit)}.")
        return Match("new", f, sku, f"🆕 New product <code>{sku}</code>.")

    if f.reference_choice and base_product and f.reference_choice.upper() == base_product.sku.upper():
        return Match("restock", f, base_product.sku,
                     f"📦 Restock of existing <code>{base_product.sku}</code>{stock(base_product)} (chosen).")

    if not variants:
        return Match("new", f, base, f"🆕 New product <code>{base}</code>.")

    seen_dial, seen_index = catalog.colors.get(f.photo_dial_color), catalog.index_types.get(f.photo_index_type)
    hits = [v for v in described
            if seen_dial and v[1] == seen_dial and (v[2] is None or seen_index is None or v[2] == seen_index)]
    if len(hits) == 1:
        p, dial, index, crown = hits[0]
        return Match("restock", _with_descriptor(f, dial, index, crown), p.sku,
                     f"📦 Restock of existing <code>{p.sku}</code>{stock(p)} (matched the {dial.name.lower()} dial).")
    if len(hits) > 1:
        return Match("choose", f, None, "", options=[p.sku for p, *_ in hits])

    if base_product and (not described or seen_dial is None):
        return Match("restock", f, base_product.sku,
                     f"📦 Restock of existing <code>{base_product.sku}</code>{stock(base_product)}. "
                     "If this is a different model, reply with its dial (e.g. \"dial blue\") to make a new product.")
    if base_product and seen_dial:
        # Base exists, other variants exist, none with this dial: the base
        # model's dial isn't recorded, so staff must say which.
        return Match("choose", f, None, "", options=[base_product.sku] + [p.sku for p, *_ in described]
                     + [f"new: {base}{descriptor_for_sku(seen_dial, None, None)}"])
    if seen_dial:
        sku = base + descriptor_for_sku(seen_dial, None, None)
        if any(p.sku.upper() == sku.upper() for p, *_ in variants):
            return Match("choose", f, None, "", options=[p.sku for p, *_ in described])
        return Match("new", _with_descriptor(f, seen_dial, None, None), sku,
                     f"🆕 New product <code>{sku}</code> (other {base} variants exist; told apart by the "
                     f"{seen_dial.name.lower()} dial).")
    return Match("choose", f, None, "", options=[p.sku for p, *_ in described])


def base_reference(catalog: Catalog, f: ItemFields) -> str | None:
    """Built code without descriptor for an AC/EXP/CA watch, else None."""
    brand = catalog.brands.get(f.brand)
    if not (brand and brand.uses_generated_sku and (f.type or "watch") == "watch" and f.ref_code):
        return None
    parts = (catalog.strap_types.get(f.strap_type), catalog.colors.get(f.strap_color),
             catalog.colors.get(f.case_color), catalog.genders.get(f.gender))
    if not all(parts):
        return None
    return generated_sku(brand, f.ref_code, *parts)
