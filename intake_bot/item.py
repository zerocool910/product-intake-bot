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
from .naming import NamingError, clock_name, generated_sku, name_sku, watch_name
from .pricing import price_breakdown

# Exact column order of the accounting importer's template.
IMPORT_COLUMNS = [
    "type", "brand", "strap_type", "strap_color", "case_color", "gender",
    "dial_color", "index_type", "crown_color", "ref_code", "manufacturer_sku",
    "supplied_price", "selling_price", "cost_price", "quantity", "category",
]
# Bot-only audit columns, appended after the import columns.
# New columns go at the END so an existing sheet's header is a prefix of
# this one and can be extended in place without shifting old rows.
AUDIT_COLUMNS = ["intake_id", "logged_by", "logged_at", "photos", "preview_name", "preview_sku", "notes",
                 "photo_dial_color", "photo_index_type"]
DEFAULT_QUANTITY = 1  # staff photograph an item in hand (owner decision 2026-11-19)

# One line per column: (filled by, required, meaning, used by the importer?).
# Drives the sheet's Legend tab and the header-cell notes.
LEGEND: dict[str, tuple[str, str, str, bool]] = {
    "type": ("Claude / caption (JD, clock)", "Always", "watch or clock. Decides the name pattern and which fields are needed.", True),
    "brand": ("Caption / Claude", "Always", "Brand name exactly as in accounting Brands.", True),
    "strap_type": ("Caption / Claude", "Watch", "Strap/bracelet material, e.g. Stainless St., Leather, Rubber.", True),
    "strap_color": ("Caption / Claude", "Watch", "Strap/bracelet color name.", True),
    "case_color": ("Caption / Claude", "Always", "Case (Bd/body) color name.", True),
    "gender": ("Caption / staff", "Watch", "G, L, A or G/L. Never taken from the photo.", True),
    "dial_color": ("Bot (auto) / staff", "No", "Unique Descriptor step 1. Filled only when another item would print the same name; adds diXXX to name and SKU.", True),
    "index_type": ("Bot (auto) / staff", "No", "Unique Descriptor step 2 (needs dial_color). Numeral, Baton, Roman.", True),
    "crown_color": ("Staff", "No", "Unique Descriptor step 3 (needs index_type).", True),
    "ref_code": ("Caption (ref …)", "AC/EXP/CA watches", "Owner-assigned model number, e.g. 1007. Printed in the name; the SKU is built from it.", True),
    "manufacturer_sku": ("Caption (sku …) / Claude", "Other brands", "Manufacturer's SKU, e.g. SRPD53K1. Printed in the name and used as Kode Barang.", True),
    "supplied_price": ("Caption", "List & Nett brands", "List brands: distributor list price (HLB). Nett brands: cost (HBB).", True),
    "selling_price": ("Caption", "SALE brands", "Selling price, typed by hand (no formula).", True),
    "cost_price": ("Bot (HBB)", "No", "HBB calculated from supplied_price. Accounting recalculates it for List/Nett brands; for SALE brands it is the optional typed cost.", True),
    "quantity": ("Caption (1pcs, qty 2) / default 1", "No", "Units in hand. Importing records a Purchase (stock + cost) in accounting.", True),
    "category": ("Staff", "No", "Optional Product Category from accounting (Products → Manage Categories), e.g. Diver Watches.", True),
    "intake_id": ("Bot", "", "Bot's id for this item (date-random).", False),
    "logged_by": ("Bot", "", "Telegram name and id of whoever sent it.", False),
    "logged_at": ("Bot", "", "When it was confirmed (UTC).", False),
    "photos": ("Bot", "", "Where the photos are saved on the server.", False),
    "preview_name": ("Bot", "", "Name the accounting app will print on the tag.", False),
    "preview_sku": ("Bot", "", "SKU (Kode Barang) the accounting app will use.", False),
    "photo_dial_color": ("Claude", "", "Dial color seen in the photo. Info only; used for the automatic descriptor.", False),
    "photo_index_type": ("Claude", "", "Index style seen in the photo. Info only; used for the automatic descriptor.", False),
    "notes": ("Claude / bot", "", "Free notes, tag cipher seen, [replaces …].", False),
}


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
    # What Claude saw; NOT import columns (dial_color/index_type are the descriptor).
    photo_dial_color: Optional[str] = None
    photo_index_type: Optional[str] = None
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
        elif (brand and r.hjb and is_watch and all([strap_type, strap_color, case_color, gender])
              and (sku := name_sku(brand, f.manufacturer_sku, f.ref_code))):
            r.name = watch_name(catalog, brand, sku, strap_type, strap_color, case_color, gender, r.hjb,
                                dial, index, crown)
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
        "cost_price": _num(_cost_price(catalog, f)),
        "quantity": f.quantity if f.quantity is not None else DEFAULT_QUANTITY,
        "category": f.category or "",
    }


def _num(v: float | None):
    if v is None:
        return ""
    return int(v) if float(v).is_integer() else v


def _cost_price(catalog: Catalog, f: ItemFields) -> float | None:
    """HBB for List/Nett brands (accounting recomputes the same value);
    the typed cost for SALE brands."""
    brand = catalog.brands.get(f.brand)
    if brand and brand.scheme != "sale":
        prices = price_breakdown(brand, f.supplied_price, f.selling_price)
        return prices.hbb if prices else None
    return f.cost_price


def auto_descriptor(catalog: Catalog, f: ItemFields, clashes: list[dict]) -> tuple[ItemFields, str] | None:
    """Unique Descriptor for an item that would print the same name as
    `clashes` (sheet rows), using what Claude saw. Cumulative: dial, then
    index if a clashing row has the same dial too. Returns (new fields, the
    descriptor text) or None if it can't help.

    Only for AC/EXP/CA: other brands print their manufacturer SKU, so a name
    clash there means the same SKU, which is the replace-the-row case. If a
    clashing row has the same dial AND index, it's probably the same model
    photographed again, so no descriptor either (staff decide)."""
    brand = catalog.brands.get(f.brand)
    if f.dial_color or not clashes or not (brand and brand.uses_generated_sku):
        return None
    dial = catalog.colors.get(f.photo_dial_color)
    if not dial:
        return None

    def seen(row: dict, key: str) -> str:
        return str(row.get(key) or row.get(f"photo_{key}") or "").strip().lower()

    data = f.model_dump()
    data["dial_color"] = dial.name
    text = f"di{dial.code}"
    same_dial = [r for r in clashes if seen(r, "dial_color") == dial.name.lower()]
    if same_dial:
        index = catalog.index_types.get(f.photo_index_type)
        if not index or any(seen(r, "index_type") == index.name.lower() for r in same_dial):
            return None  # no index seen, or same dial + index: staff decide
        data["index_type"] = index.name
        text += f"-idx{index.code}"
    return ItemFields(**data), text


def sample_rows(catalog: Catalog) -> list[tuple[str, dict[str, object]]]:
    """Worked examples for the Legend tab, generated by the real code so they
    always match what the bot writes."""
    cases = [
        ("Watch, manufacturer SKU (Seiko, list price 1,250,000)",
         ItemFields(type="watch", brand="Seiko", manufacturer_sku="SRPD53K1", strap_type="Stainless St.",
                    strap_color="Silver", case_color="Silver", gender="Gents", supplied_price=1_250_000,
                    photo_dial_color="Blue", photo_index_type="Baton")),
        ("Watch, AC/EXP/CA ref code (list price 950,000, 2 pcs)",
         ItemFields(type="watch", brand="Alexandre Christie", ref_code="1007", strap_type="Stainless St.",
                    strap_color="Black", case_color="Black", gender="Gents", supplied_price=950_000, quantity=2)),
        ("Same as above but a different model sharing ref 1007: descriptor added automatically",
         ItemFields(type="watch", brand="Alexandre Christie", ref_code="1007", strap_type="Stainless St.",
                    strap_color="Black", case_color="Black", gender="Gents", supplied_price=950_000,
                    dial_color="Blue", photo_dial_color="Blue")),
        ("Nett brand (Bonia, cost 400,000 → HJB 600,000)",
         ItemFields(type="watch", brand="Bonia", manufacturer_sku="BNB10123", strap_type="Leather",
                    strap_color="Brown", case_color="Gold", gender="Ladies", supplied_price=400_000)),
        ("SALE brand (Police, selling price 650,000)",
         ItemFields(type="watch", brand="Police", manufacturer_sku="PL15000", strap_type="Leather",
                    strap_color="Brown", case_color="Gold", gender="Gents", selling_price=650_000)),
        ("Clock (Seiko, list price 250,000)",
         ItemFields(type="clock", brand="Seiko", manufacturer_sku="QXA756W", case_color="White",
                    supplied_price=250_000)),
    ]
    out = []
    for label, f in cases:
        r = review(catalog, f)
        row = to_import_row(catalog, f)
        row.update({"preview_name": r.name or "", "preview_sku": r.sku or "",
                    "photo_dial_color": f.photo_dial_color or "", "photo_index_type": f.photo_index_type or ""})
        out.append((label, row))
    return out
