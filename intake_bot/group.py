"""Multi-item photos: several watches in one photo, one caption.

Staff photograph e.g. five colours of one model and caption
"Ac 5014 / semua warna 2.000.000 / 5 pcs". Each watch becomes its own item
(its own receipt row); the caption applies to all of them; "5 pcs" is a
checker for the count, not a quantity per watch (each watch seen = 1 pc).
"""

from __future__ import annotations

import re

from .catalog import Catalog
from .item import DEFAULT_QUANTITY, ItemFields, auto_descriptor, review

_QTY_ONLY = re.compile(r"^(?:qty|jumlah|pcs|pc)\s*[:=]?\s*(\d+)$|^(\d+)\s*(?:pcs|pc|buah)$|^x\s?(\d+)$", re.I)
_GENDER_WORDS = {"g": "Gents", "gents": "Gents", "pria": "Gents", "men": "Gents",
                 "l": "Ladies", "ladies": "Ladies", "wanita": "Ladies", "women": "Ladies",
                 "a": "All", "unisex": "All", "g/l": "Gents/Ladies"}


def split_caption(caption: ItemFields) -> tuple[ItemFields, int | None]:
    """In a group the caption's quantity is the total-count checker, not a
    per-watch quantity. Returns (caption fields for every item, total)."""
    data = caption.model_dump(exclude_unset=True)
    total = data.pop("quantity", None)
    return ItemFields(**data), total


def parse_command(text: str) -> tuple[str, int | None, str] | None:
    """'3 dial black' -> ('item', 3, 'dial black'); 'semua L' -> ('all', None, 'L');
    'hapus 5' -> ('remove', 5, ''); a bare gender ('L') -> ('all', None, 'L')."""
    t = " ".join(text.split())
    if m := re.match(r"^(?:hapus|remove|hps|delete)\s+(\d+)$", t, re.I):
        return "remove", int(m.group(1)), ""
    if m := re.match(r"^(?:semua|all|semuanya)\s+(.+)$", t, re.I):
        return "all", None, m.group(1)
    if m := re.match(r"^#?(\d{1,2})[.):]?\s+(.+)$", t):
        return "item", int(m.group(1)), m.group(2)
    if t.lower() in _GENDER_WORDS:
        return "all", None, t
    return None


def quick_change(text: str) -> dict | None:
    """Changes that don't need Claude: a gender word or a quantity."""
    t = text.strip().lower()
    if t in _GENDER_WORDS:
        return {"gender": _GENDER_WORDS[t]}
    if m := _QTY_ONLY.match(t):
        return {"quantity": int(next(g for g in m.groups() if g))}
    return None


def apply_change(f: ItemFields, change: dict) -> ItemFields:
    data = f.model_dump()
    data.update(change)
    return ItemFields(**data)


def count_check(seen: int, count_sure: bool, caption_total: int | None, count_ok: bool,
                items: list[ItemFields]) -> tuple[bool, str]:
    """(ok, line for the summary). Each watch seen is 1 pc unless staff set a qty."""
    pcs = sum(f.quantity if f.quantity is not None else DEFAULT_QUANTITY for f in items)
    if count_ok:
        return True, f"✔ {len(items)} item(s), {pcs} pcs (confirmed)."
    if caption_total is not None:
        if pcs == caption_total:
            return True, f"✔ {pcs} pcs = caption {caption_total} pcs."
        return False, (f"⚠️ Caption says <b>{caption_total} pcs</b> but the items add up to <b>{pcs}</b>. "
                       f"Set quantities (e.g. \"2 qty 2\"), remove extras (\"hapus 5\"), or tap the button if "
                       f"{pcs} is right.")
    if not count_sure:
        return False, f"❔ I count <b>{len(items)}</b> item(s) but I'm not sure. Is that right?"
    return True, f"{len(items)} item(s), {pcs} pcs."


def descriptors_within_group(catalog: Catalog, items: list[ItemFields]) -> tuple[list[ItemFields], dict[int, int]]:
    """Items in one photo that would get the same reference get a Unique
    Descriptor from what Claude saw (dial, then index), like rows already in
    the sheet do. Returns (items, {index: index of the item it still clashes with})."""
    items = list(items)
    for _ in range(2):  # a second pass catches items that needed the index too
        by_sku: dict[str, list[int]] = {}
        for i, f in enumerate(items):
            sku = review(catalog, f).sku
            if sku:
                by_sku.setdefault(sku.upper(), []).append(i)
        for idx in by_sku.values():
            if len(idx) < 2:
                continue
            for i in idx:
                others = [{"dial_color": items[o].dial_color or "", "photo_dial_color": items[o].photo_dial_color or "",
                           "index_type": items[o].index_type or "",
                           "photo_index_type": items[o].photo_index_type or ""} for o in idx if o != i]
                f = items[i]
                if f.dial_color and any((o["dial_color"] or o["photo_dial_color"]).lower() == f.dial_color.lower()
                                        for o in others) and not f.index_type:
                    # Same dial as another: try the index (cumulative rule).
                    data = f.model_dump(); data["dial_color"] = None
                    f = ItemFields(**data)
                result = auto_descriptor(catalog, f, others)
                if result:
                    items[i] = result[0]
    clashes: dict[int, int] = {}
    seen: dict[str, int] = {}
    for i, f in enumerate(items):
        sku = review(catalog, f).sku
        if sku and sku.upper() in seen:
            clashes[i] = seen[sku.upper()]
        elif sku:
            seen[sku.upper()] = i
    return items, clashes


_SKU_GENDER = re.compile(r"[A-Z]{2}\d{2}C\d{2}(G/L|G|L|A)", re.I)


def gender_from_codes(prefix: str, skus: list[str]) -> str | None:
    """The one gender shared by existing AC/EXP/CA references for a model
    (e.g. every AC5014… code ends in L), or None if none / mixed."""
    codes = {"G": "Gents", "L": "Ladies", "A": "All", "G/L": "Gents/Ladies"}
    found = set()
    for sku in skus:
        if not sku.upper().startswith(prefix.upper()):
            continue
        if m := _SKU_GENDER.match(sku[len(prefix):]):
            found.add(codes[m.group(1).upper()])
    return found.pop() if len(found) == 1 else None
