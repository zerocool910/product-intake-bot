"""Printed name, price cipher and AC/EXP/CA SKU preview.

Implements docs/product-naming-convention.md. The accounting app generates
the authoritative values on import; this exists so staff can see, at confirm
time, exactly what tag text their answers will produce.
"""

from __future__ import annotations

from .catalog import Brand, Catalog, Code


class NamingError(ValueError):
    pass


def encode_cipher(catalog: Catalog, hjb: float) -> str:
    """HJB in thousands (last 3 digits dropped, not rounded), digit-substituted."""
    thousands = int(round(hjb)) // 1000
    return "".join(catalog.cipher[d] for d in str(thousands))


def decode_cipher(catalog: Catalog, letters: str) -> int | None:
    """Tag letters -> HJB in Rupiah (accurate to the nearest Rp1,000)."""
    reverse = {v: k for k, v in catalog.cipher.items()}
    try:
        return int("".join(reverse[c] for c in letters.strip().upper())) * 1000
    except (KeyError, ValueError):
        return None


def _check_descriptor_order(dial: Code | None, index: Code | None, crown: Code | None) -> None:
    if index and not dial:
        raise NamingError("index_type needs dial_color (Unique Descriptor is cumulative: dial → index → crown)")
    if crown and not index:
        raise NamingError("crown_color needs index_type (Unique Descriptor is cumulative: dial → index → crown)")


def descriptor_for_name(dial: Code | None, index: Code | None, crown: Code | None) -> str:
    _check_descriptor_order(dial, index, crown)
    parts = []
    if dial:
        parts.append(f"di{dial.code}")
    if index:
        parts.append(f"idx{index.code}")
    if crown:
        parts.append(f"cr{crown.code}")
    return "-".join(parts)


def descriptor_for_sku(dial: Code | None, index: Code | None, crown: Code | None) -> str:
    _check_descriptor_order(dial, index, crown)
    out = ""
    if dial:
        out += f"di{dial.number}"
    if index:
        out += f"idx{index.code}"
    if crown:
        out += f"cr{crown.number}"
    return out


def watch_name(catalog: Catalog, brand: Brand, strap_type: Code, strap_color: Code, case_color: Code,
               gender: Code, hjb: float, dial: Code | None = None, index: Code | None = None,
               crown: Code | None = None) -> str:
    descriptor = descriptor_for_name(dial, index, crown)
    middle = f"{brand.printed} {strap_type.code} {strap_color.code} Bd {case_color.code}"
    if descriptor:
        middle += f" {descriptor}"
    return f"{middle}. {gender.code} - {encode_cipher(catalog, hjb)}"


def clock_name(catalog: Catalog, brand: Brand, case_color: Code, hjb: float) -> str:
    return f"{brand.printed} JD Bd {case_color.code} - {encode_cipher(catalog, hjb)}"


def generated_sku(brand: Brand, ref_code: str, strap_type: Code, strap_color: Code, case_color: Code,
                  gender: Code, dial: Code | None = None, index: Code | None = None,
                  crown: Code | None = None) -> str:
    """{BrandCode}{RefCode}{StrapType}{StrapColor#}C{CaseColor#}{Gender}{descriptor}"""
    return (f"{brand.printed}{ref_code}{strap_type.code}{strap_color.number}C{case_color.number}"
            f"{gender.code}{descriptor_for_sku(dial, index, crown)}")
