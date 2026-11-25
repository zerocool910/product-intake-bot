"""SKU / model lookup ("cek SRPD53K1", "cek AC 5014"): read-only.

Shows what the accounting product master has for a reference (or every
variant of an AC/EXP/CA model) and what the bot has received for it.
"""

from __future__ import annotations

import html
import re

from .catalog import Catalog

MIN_PREFIX = 3        # the accounting API returns nothing for shorter prefixes
MAX_LISTED = 15

_CMD = re.compile(r"^/?(?:cek|check|cari|lookup)(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)


def parse_lookup(text: str) -> str | None:
    """'cek SRPD53K1' -> 'SRPD53K1'; '/cek' alone -> ''; anything else -> None."""
    m = _CMD.match(text.strip())
    return (m.group(1) or "").strip() if m else None


_GENDERS = {"G": "G", "GENTS": "G", "PRIA": "G", "L": "L", "LADIES": "L", "WANITA": "L",
            "A": "A", "UNISEX": "A", "G/L": "G/L"}


def build_prefix(catalog: Catalog, query: str) -> tuple[str, str, str | None]:
    """(reference prefix, label, gender filter code or None).

    The first word after the brand is the model / SKU; a gender word after it
    filters ('cek AC 2C07 L' -> AC2C07…L codes), it isn't part of the code.
    'AC 5014' -> ('AC5014', 'Alexandre Christie 5014', None);
    'Seiko SRPD53K1' / 'srpd53k1' -> ('SRPD53K1', 'SRPD53K1', None)."""
    words = query.split()
    gender = None
    while len(words) > 1 and words[-1].upper() in _GENDERS:
        gender = _GENDERS[words.pop().upper()]
    for n in (3, 2, 1):  # brand names can be several words
        if len(words) > n:
            brand = catalog.brands.get(" ".join(words[:n]))
            if brand:
                code = words[n].upper()
                if brand.uses_generated_sku:
                    return f"{brand.code}{code}".upper(), f"{brand.name} {code}", gender
                return code, f"{brand.name} {code}", gender
    flat = (words[0] if words else "").upper()
    return flat, flat, gender


_SKU_GENDER = re.compile(r"[A-Z]{2}\d{2}C\d{2}(G/L|G|L|A)", re.IGNORECASE)


def matches_gender(prefix: str, sku: str, gender: str | None) -> bool:
    """For built AC/EXP/CA codes the gender sits after the case color
    (…SS20C20L…); other SKUs don't carry one, so they always pass."""
    if not gender:
        return True
    m = _SKU_GENDER.match(sku[len(prefix):])
    return bool(m) and m.group(1).upper() == gender if m else True


def _qty(v) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def format_result(label: str, prefix: str, products: list | None, receipts: list[dict] | None) -> str:
    """products: master products starting with prefix, or None if the master
    couldn't be reached / isn't set up. receipts: sheet rows for the prefix."""
    e = html.escape
    out = [f"🔎 <b>{e(label)}</b>"]
    if products is None:
        out.append("⚠️ Accounting not reachable right now, so I can't say whether it exists there.")
    elif not products:
        out.append("🆕 Not in accounting yet: it becomes a new product on its next import.")
    else:
        exact = [p for p in products if p.sku.upper() == prefix]
        others = [p for p in products if p.sku.upper() != prefix]
        if exact:
            p = exact[0]
            out.append(f"📦 In accounting: <code>{e(p.sku)}</code>")
            if p.name:
                out.append(f"   {e(p.name)}")
            if p.stock_quantity is not None:
                out.append(f"   Stock: <b>{p.stock_quantity:g}</b>")
        if others:
            out.append(f"{'Also starting with' if exact else 'In accounting'} <code>{e(prefix)}</code> "
                       f"({len(others)}):")
            for p in others[:MAX_LISTED]:
                stock = f" — stock {p.stock_quantity:g}" if p.stock_quantity is not None else ""
                out.append(f"• <code>{e(p.sku)}</code>{stock}")
                if p.name:
                    out.append(f"   {e(p.name)}")
            if len(others) > MAX_LISTED:
                out.append(f"… and {len(others) - MAX_LISTED} more. Type a longer code to narrow it down.")
    if receipts is None:
        pass
    elif receipts:
        pcs = sum(_qty(r.get("quantity")) for r in receipts)
        dates = sorted(str(r.get("receipt_date") or "") for r in receipts if r.get("receipt_date"))
        last = f", last on {dates[-1]}" if dates else ""
        out.append(f"🧾 Received via the bot: {len(receipts)} receipt(s), {pcs} pcs{last}.")
    else:
        out.append("🧾 Not received via the bot yet.")
    return "\n".join(out)
