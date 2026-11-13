"""One intake item: its fields, validation against the accounting import
template, the confirm-time preview, and the spreadsheet row it becomes.

Column rules mirror globaltimecollection-accounting's
docs/product-import-template.md (copied in docs/accounting-import-columns.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .catalog import Catalog
from .naming import NamingError, clock_name, generated_sku, watch_name
from .pricing import price_breakdown

# Exact column order of the accounting importer's template.
IMPORT_COLUMNS = [
    "type", "brand", "strap_type", "strap_color", "case_color", "gender",
    "dial_color", "index_type", "crown_color", "ref_code", "manufacturer_sku",
    "supplied_price", "selling_price", "cost_price", "quantity", "category",
]
# Bot-only audit columns, appended after the import columns.
AUDIT_COLUMNS = ["intake_id", "logged_by", "logged_at", "photos", "preview_name", "preview_sku", "notes"]


class ItemFields(BaseModel):
    """Everything staff can set for an item. Names, not codes."""

    type: Optional[Literal["watch", "clock"]] = None
    brand: Optional[str] = None
    strap_type: Optional[str] = None
    strap_color: Optional[str] = None
    case_color: Optional[str] = None
    gender: Optional[str] = None
    dial_color: Optional[str] = None
    index_type: Optional[str] = None
    crown_color: Optional[str] = None
    ref_code: Optional[str] = None
    manufacturer_sku: Optional[str] = None
    supplied_price: Optional[float] = None
    selling_price: Optional[float] = None
    cost_price: Optional[float] = None
    quantity: Optional[int] = None
    category: Optional[str] = None
    uncertain: list[str] = Field(default_factory=list, description="Field names the model is not confident about.")
    notes: Optional[str] = None

    def merged(self, override: "ItemFields") -> "ItemFields":
        """Fields set in `override` win; `uncertain` drops anything override set."""
        data = self.model_dump()
        for k, v in override.model_dump(exclude_unset=True).items():
            if k in ("uncertain", "notes") or v is None:
                continue
            data[k] = v
        set_keys = {k for k, v in override.model_dump(exclude_unset=True).items() if v is not None}
        data["uncertain"] = [u for u in self.uncertain if u not in set_keys]
        return ItemFields(**data)


@dataclass
class Review:
    """Result of checking an item: what's missing/wrong, and the preview."""

    errors: list[str] = field(default_factory=list)
    hbb: float | None = None
    hjb: float | None = None
    name: str | None = None
    sku: str | None = None

    @property
    def ok(self) -> bool:
        return not self.errors


def review(catalog: Catalog, f: ItemFields) -> Review:
    r = Review()
    brand = catalog.brands.get(f.brand)
    kind = f.type or (brand.kind if brand else None)

    if not f.brand:
        r.errors.append("brand is missing")
    elif not brand:
        r.errors.append(f"brand {f.brand!r} is not in config/brands.yaml")
    if not kind:
        r.errors.append("type (watch/clock) is missing")

    def lookup(table, value, label, required):
        if not value:
            if required:
                r.errors.append(f"{label} is missing")
            return None
        item = table.get(value)
        if not item:
            r.errors.append(f"{label} {value!r} is not a known value")
        return item

    is_watch = kind == "watch"
    strap_type = lookup(catalog.strap_types, f.strap_type, "strap_type", is_watch)
    strap_color = lookup(catalog.colors, f.strap_color, "strap_color", is_watch)
    case_color = lookup(catalog.colors, f.case_color, "case_color", True)
    gender = lookup(catalog.genders, f.gender, "gender", is_watch)
    dial = lookup(catalog.colors, f.dial_color, "dial_color", False)
    index = lookup(catalog.index_types, f.index_type, "index_type", False)
    crown = lookup(catalog.colors, f.crown_color, "crown_color", False)

    if brand:
        if brand.uses_generated_sku and is_watch:
            if not f.ref_code:
                r.errors.append(f"ref_code is missing ({brand.code} uses an owner-assigned reference number)")
        elif not f.manufacturer_sku:
            r.errors.append("manufacturer_sku is missing")

        if brand.scheme == "sale":
            if not f.selling_price:
                r.errors.append("selling_price is missing (SALE brand — no formula)")
        elif not f.supplied_price:
            what = "HLB / list price" if brand.scheme == "list" else "HBB / cost"
            r.errors.append(f"supplied_price is missing ({what} for {brand.name})")

        prices = price_breakdown(brand, f.supplied_price, f.selling_price)
        if prices:
            r.hbb, r.hjb = prices.hbb, prices.hjb

    if f.quantity is not None and f.quantity < 0:
        r.errors.append("quantity cannot be negative")

    # Preview — only when everything it needs resolved. SKU first: a clock's
    # printed name includes it.
    try:
        if brand and brand.uses_generated_sku and is_watch:
            if f.ref_code and all([strap_type, strap_color, case_color, gender]):
                r.sku = generated_sku(brand, f.ref_code, strap_type, strap_color, case_color, gender, dial, index, crown)
        elif brand and f.manufacturer_sku:
            r.sku = f.manufacturer_sku
        if brand and r.hjb and kind == "clock" and case_color and r.sku:
            r.name = clock_name(catalog, brand, r.sku, case_color, r.hjb)
        elif brand and r.hjb and is_watch and all([strap_type, strap_color, case_color, gender]):
            r.name = watch_name(catalog, brand, strap_type, strap_color, case_color, gender, r.hjb, dial, index, crown)
    except NamingError as e:
        r.errors.append(str(e))

    return r


def to_import_row(catalog: Catalog, f: ItemFields) -> dict[str, object]:
    """Canonical names (as the accounting importer matches them), blanks for unset."""
    brand = catalog.brands.get(f.brand)
    kind = f.type or (brand.kind if brand else "")

    def canon(table, value):
        item = table.get(value)
        return item.name if item else (value or "")

    return {
        "type": kind,
        "brand": brand.name if brand else (f.brand or ""),
        "strap_type": canon(catalog.strap_types, f.strap_type) if kind == "watch" else "",
        "strap_color": canon(catalog.colors, f.strap_color) if kind == "watch" else "",
        "case_color": canon(catalog.colors, f.case_color),
        "gender": (catalog.genders.get(f.gender).code if catalog.genders.get(f.gender) else (f.gender or "")) if kind == "watch" else "",
        "dial_color": canon(catalog.colors, f.dial_color),
        "index_type": canon(catalog.index_types, f.index_type),
        "crown_color": canon(catalog.colors, f.crown_color),
        "ref_code": f.ref_code or "",
        "manufacturer_sku": f.manufacturer_sku or "",
        "supplied_price": _num(f.supplied_price),
        "selling_price": _num(f.selling_price),
        "cost_price": _num(f.cost_price),
        "quantity": f.quantity if f.quantity is not None else "",
        "category": f.category or "",
    }


def _num(v: float | None):
    if v is None:
        return ""
    return int(v) if float(v).is_integer() else v
