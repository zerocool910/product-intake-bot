"""HBB/HJB preview from the one number staff type in the caption.

Mirrors the accounting app's Brand::priceBreakdown() for the two values the
tag and the import need. Retail/wholesale are deliberately left out: DJE is a
range and Nett-scheme wholesale is still unconfirmed (see
docs/brand-pricing-reference.md).
"""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import Brand


@dataclass(frozen=True)
class PriceBreakdown:
    hbb: float | None  # cost (Harga Beli Barang)
    hjb: float         # list/reference selling price (Harga Jual Barang)


def price_breakdown(brand: Brand, supplied_price: float | None, selling_price: float | None = None) -> PriceBreakdown | None:
    """Return HBB/HJB, or None if the inputs needed for this brand are missing.

    list: supplied_price is HLB.  HBB = HLB × Π(1 − d) (sequential).  HJB = HLB.
    nett: supplied_price is HBB.  HJB = HBB × (1 + markup).
    sale: no formula. HJB = selling_price typed by hand, HBB unknown.
    """
    if brand.scheme == "sale":
        return PriceBreakdown(hbb=None, hjb=float(selling_price)) if selling_price else None
    if not supplied_price:
        return None
    if brand.scheme == "list":
        hbb = float(supplied_price)
        for d in brand.purchase_discounts:
            hbb *= 1 - d
        return PriceBreakdown(hbb=round(hbb, 2), hjb=float(supplied_price))
    if brand.scheme == "nett":
        hjb = float(supplied_price) * (1 + (brand.markup or 0))
        return PriceBreakdown(hbb=float(supplied_price), hjb=round(hjb, 2))
    raise ValueError(f"Unknown pricing scheme {brand.scheme!r} for {brand.name}")
