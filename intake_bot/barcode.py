"""Barcodes: decode them from the item photos, or take what staff type.

The barcode is recorded per receipt line (bot column `barcode`, after the
import columns). Priority: typed by staff (caption / correction) > decoded
from the photo > digits Claude read under the bars, the last only when the
GTIN check digit is right (a misread digit almost always breaks it).
Nothing found -> blank; never guessed.
"""

from __future__ import annotations

import io
import logging
import re

log = logging.getLogger(__name__)

# "barcode 4901234567894", "bc: 4901234567894", "ean 4901-2345-67894"
TYPED = re.compile(r"(?<!\w)(?:barcode|barkode|bar\s*code|bc|ean|upc)\s*[:=]?\s*([0-9A-Za-z][0-9A-Za-z\- ]{2,40}[0-9A-Za-z])",
                   re.IGNORECASE)


def gtin_ok(code: str) -> bool:
    """EAN-8 / UPC-A / EAN-13 / GTIN-14 with a valid check digit."""
    if not code.isdigit() or len(code) not in (8, 12, 13, 14):
        return False
    body, check = code[:-1], int(code[-1])
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def clean(value: str | None) -> str | None:
    """Normalise a typed or read value: spaces and dashes go; an all-digit
    value must be a valid GTIN or 4-20 digits (in-house codes); other codes
    (Code 128 etc.) 4-40 letters/digits. Anything else -> None."""
    if not value:
        return None
    v = re.sub(r"[\s\-]", "", value).upper()
    if v.isdigit():
        return v if gtin_ok(v) or 4 <= len(v) <= 20 else None
    return v if re.fullmatch(r"[0-9A-Z]{4,40}", v) else None


def from_text(text: str | None) -> tuple[str | None, str]:
    """(barcode, text without it) for a caption or correction."""
    if not text:
        return None, text or ""
    m = TYPED.search(text)
    if not m:
        return None, text
    # Take the leading run of the code only: "bc 4901234567894 ss silver" stops at the space
    # unless the next chunk is digits (codes typed in groups: "4 901234 567894").
    raw = m.group(1)
    parts, kept = raw.split(" "), []
    for p in parts:
        if kept and not p.replace("-", "").isdigit():
            break
        kept.append(p)
    code = clean(" ".join(kept))
    if not code:
        return None, text
    consumed = m.group(0)[: m.group(0).index(raw)] + " ".join(kept)
    return code, text.replace(consumed, " ", 1)


def scan(images: list[bytes]) -> list[str]:
    """Distinct barcodes decoded from the photos, in the order found.
    Product (linear) codes only; QR codes on warranty cards are skipped."""
    try:
        import zxingcpp
        from PIL import Image
    except ImportError:  # decoder not installed: Claude's reading still applies
        log.warning("zxing-cpp / Pillow not installed, barcode scanning off")
        return []
    found: list[str] = []
    for data in images:
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
            gray = img.convert("L")
            results = zxingcpp.read_barcodes(gray, formats=zxingcpp.BarcodeFormat.AllLinear)
        except Exception as err:
            log.warning("Barcode scan failed on one photo: %s", err)
            continue
        for r in results:
            code = clean(r.text)
            if code and code not in found:
                found.append(code)
    return found


def looks_mistyped(code: str | None) -> bool:
    """A GTIN-length number whose check digit is wrong (a typo, usually)."""
    return bool(code) and code.isdigit() and len(code) in (8, 12, 13, 14) and not gtin_ok(code)


def choose(scanned: list[str], claude_digits: str | None) -> tuple[str | None, bool, str | None]:
    """(barcode, unsure, note) for ONE item from what the photos gave."""
    if len(scanned) == 1:
        return scanned[0], False, None
    if len(scanned) > 1:
        return None, False, (f"several barcodes in the photos ({', '.join(scanned)}): "
                             "type the right one, e.g. \"barcode " + scanned[0] + "\"")
    digits = re.sub(r"\D", "", claude_digits or "")
    if digits and gtin_ok(digits):
        return digits, True, None  # read by eye, check digit fits: shown with ❓
    return None, False, None
