"""Loads the code tables and brand table from config/*.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


@dataclass(frozen=True)
class Code:
    name: str
    code: str
    number: str | None = None  # colors only


@dataclass(frozen=True)
class Brand:
    name: str
    code: str
    print_as: str
    scheme: str  # list | nett | sale
    purchase_discounts: tuple[float, ...] = ()
    markup: float | None = None
    uses_generated_sku: bool = False
    kind: str = "watch"
    aliases: tuple[str, ...] = ()


def _norm(s: str) -> str:
    return " ".join(s.lower().replace("_", " ").split())


@dataclass
class Lookup:
    """Case-insensitive lookup by name or code."""

    items: list
    _index: dict = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        for item in self.items:
            keys = [item.name, item.code, *getattr(item, "aliases", ())]
            for k in keys:
                self._index.setdefault(_norm(k), item)

    def get(self, key: str | None):
        if not key:
            return None
        return self._index.get(_norm(key))

    def names(self) -> list[str]:
        return [i.name for i in self.items]


@dataclass
class Catalog:
    genders: Lookup
    strap_types: Lookup
    colors: Lookup
    index_types: Lookup
    brands: Lookup
    cipher: dict[str, str]

    @classmethod
    def load(cls, config_dir: Path = CONFIG_DIR) -> "Catalog":
        codes = yaml.safe_load((config_dir / "codes.yaml").read_text(encoding="utf-8"))
        brands = yaml.safe_load((config_dir / "brands.yaml").read_text(encoding="utf-8"))

        def codes_of(key: str) -> Lookup:
            return Lookup([Code(**{k: str(v) for k, v in row.items()}) for row in codes[key]])

        brand_rows = []
        for row in brands["brands"]:
            row = dict(row)
            row["purchase_discounts"] = tuple(float(d) for d in row.get("purchase_discounts", ()))
            row["aliases"] = tuple(row.get("aliases", ()))
            brand_rows.append(Brand(**row))

        return cls(
            genders=codes_of("genders"),
            strap_types=codes_of("strap_types"),
            colors=codes_of("colors"),
            index_types=codes_of("index_types"),
            brands=Lookup(brand_rows),
            cipher={str(k): str(v) for k, v in codes["cipher"].items()},
        )
