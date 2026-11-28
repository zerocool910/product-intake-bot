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
    if m := re.match(r"^(\d{1,2}(?:\s*[,+&]\s*\d{1,2})+)\s+(.+)$", t):     # "1,3 1.900.000"
        return "items", [int(x) for x in re.findall(r"\d+", m.group(1))], m.group(2)
    if m := re.match(r"^#?(\d{1,2})[.):]?\s+(.+)$", t):
        return "item", int(m.group(1)), m.group(2)
    if t.lower() in _GENDER_WORDS:
        return "all", None, t
    return None


def quick_change(text: str) -> dict | None:
    """Changes that don't need Claude: any mix of a price, a gender word and
    a quantity ("1.180.000 L", "L", "qty 3", "980k G 2pcs")."""
    from .barcode import from_text
    from .caption import parse_price
    code, rest = from_text(text)
    if code:
        if rest.strip():
            other = quick_change(rest)
            return None if other is None else {**other, "barcode": code}
        return {"barcode": code}
    t = text.strip().lower()
    if m := _QTY_ONLY.match(t):
        return {"quantity": int(next(g for g in m.groups() if g))}
    change: dict = {}
    for tok in t.split():
        if tok in _GENDER_WORDS and "gender" not in change:
            change["gender"] = _GENDER_WORDS[tok]
        elif (m := _QTY_ONLY.match(tok)) and "quantity" not in change:
            change["quantity"] = int(next(g for g in m.groups() if g))
        elif (price := parse_price(tok)) is not None and "supplied_price" not in change:
            change["supplied_price"] = price
        else:
            return None
    return change or None


def apply_change(f: ItemFields, change: dict) -> ItemFields:
    data = f.model_dump()
    data.update(change)
    data["uncertain"] = [u for u in data["uncertain"] if u not in change]  # staff set it: no ❓
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


# ---------- per-colour caption lines ----------
#
# "AC 1044
#  Silver 980.000 L
#  Full black 1.180.000 G
#  Rose Gold 1.180.000 L"
# Lines that start with a colour (optionally "full …", or "two tone" /
# "kombi") are rules for the watches of that colour; the other lines apply
# to every watch.

_FULL = re.compile(r"^(?:full|all|semua|serba)\s+", re.I)
_TWO_TONE = re.compile(r"^(?:two[\s-]?tone|kombi|combi|dt)\b", re.I)


class VariantRule:
    def __init__(self, line: str, colors: list[str], full: bool, two_tone: bool, fields: ItemFields,
                 problem: str | None = None, read_as: list[str] | None = None):
        self.line, self.colors, self.full, self.two_tone, self.fields = line, colors, full, two_tone, fields
        self.problem = problem          # e.g. an unreadable price: items it matches are flagged
        self.read_as = read_as or []    # typo corrections, e.g. "'Rose Golf' read as Rose Gold"

    def matches(self, f: ItemFields) -> int:
        """0 = no; higher = more specific (full colour > two-tone > case > strap)."""
        def same(a, b):
            return bool(a and b and a.lower() == b.lower())
        if self.two_tone:
            return 3 if (f.strap_type or "").lower() == "rantai kombi" else 0
        if self.full:
            return 4 if any(same(f.strap_color, c) and same(f.case_color, c) for c in self.colors) else 0
        if any(same(f.case_color, c) for c in self.colors):
            return 2
        if not f.case_color and any(same(f.strap_color, c) for c in self.colors):
            return 1
        return 0


_SEP = re.compile(r"\s*(?:/|,|&|\+|\bdan\b|\band\b|\bor\b|\batau\b)\s*", re.I)
_NUMBERISH = re.compile(r"(?<![\w.,])\d[\d.,]*\d(?:\s*(?:k|rb|jt|juta|ribu))?(?![\w.,])", re.I)


def _read_colors(catalog: Catalog, text: str) -> tuple[list[str], list[str], list[str]]:
    """'Gold / Rose Golf' -> (['Gold', 'Rose Gold'], ["'Rose Golf' read as Rose Gold"], [unknown])."""
    import difflib
    names = {c.name.lower(): c.name for c in catalog.colors.items}
    names.update({c.code.lower(): c.name for c in catalog.colors.items})
    found, read_as, unknown = [], [], []
    for part in filter(None, (x.strip() for x in _SEP.split(text))):
        key = part.lower()
        if key in names:
            found.append(names[key])
            continue
        close = difflib.get_close_matches(key, list(names), n=1, cutoff=0.8)
        if close:
            found.append(names[close[0]])
            read_as.append(f"'{part}' read as {names[close[0]]}")
        else:
            unknown.append(part)
    return found, read_as, unknown


def split_variant_lines(catalog: Catalog, caption: str | None, parse) -> tuple[str, list[VariantRule]]:
    """(text of the common lines, colour rules). `parse` is parse_caption.

    A colour line is: [full] <colour>[ / <colour> …] <price> <gender> …,
    e.g. "Silver 1.700.000 L", "Gold / Rose Gold 1.900.000 L", "Full black 1.180.000 G".
    Colours are read up to the first number or gender word; small typos are
    corrected ("Rose Golf") and reported. A number that isn't a valid price
    ("1.900.0000") is never guessed: the line's watches are flagged."""
    from .caption import parse_price
    if not caption:
        return "", []
    common, rules = [], []
    for raw in caption.splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        rest, full = line, False
        if m := _FULL.match(rest):
            rest, full = rest[m.end():], True
        two_tone = bool(_TWO_TONE.match(rest))
        # The colour part: everything before the first number / gender word.
        head = re.split(r"(?<!\w)(?:\d|G/L\b|G\b|L\b|A\b|gents\b|ladies\b|unisex\b)", rest, maxsplit=1)[0]
        colors, read_as, unknown = ([], [], []) if two_tone else _read_colors(catalog, head)
        fields = parse(catalog, line)
        bad = [t for t in _NUMBERISH.findall(line) if parse_price(t) is None and len(re.sub(r"\D", "", t)) >= 4]
        has_values = (fields.supplied_price is not None or fields.selling_price is not None or fields.gender
                      or bad)
        if (colors or two_tone) and has_values and not fields.brand:
            data = fields.model_dump(exclude_unset=True)
            for k in ("strap_type", "strap_color", "case_color"):  # the colour picks watches; it doesn't set them
                data.pop(k, None)
            problem = None
            if bad:
                data.pop("supplied_price", None)
                problem = f"price '{bad[0]}' in \"{line}\" isn't a valid number"
            elif unknown:
                problem = f"colour '{unknown[0]}' in \"{line}\" not recognised"
            rules.append(VariantRule(line, colors, full and not two_tone, two_tone, ItemFields(**data),
                                     problem, read_as))
        else:
            common.append(raw)
    return "\n".join(common), rules


def apply_rules(items: list[ItemFields], rules: list[VariantRule]) -> tuple[list[ItemFields], dict[int, str]]:
    """Each item gets the values of its most specific matching rule.
    Returns (items, {index: problem}) for items no rule (or two equal rules) matched."""
    out, problems = [], {}
    for i, f in enumerate(items):
        scored = sorted(((r.matches(f), n) for n, r in enumerate(rules)), reverse=True)
        best = [n for s, n in scored if s and s == scored[0][0]]
        if not rules:
            out.append(f)
        elif not best:
            out.append(f)
            problems[i] = "no caption line matched its colours"
        elif len(best) > 1:
            out.append(f)
            problems[i] = "matches two caption lines: " + " / ".join(rules[n].line for n in best)
        else:
            rule = rules[best[0]]
            out.append(f.merged(rule.fields))
            if rule.problem:
                problems[i] = rule.problem
    return out, problems
