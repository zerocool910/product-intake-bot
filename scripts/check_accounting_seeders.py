"""Compare config/*.yaml against the accounting app's seeders.

    python scripts/check_accounting_seeders.py /path/to/globaltimecollection-accounting

The importer matches brands, strap types, colors and index types by name,
so any drift here makes rows fail on import. Exits 1 if anything differs.
Only reads the seeders' literal arrays; rows added later through the
accounting admin UI aren't seen, so check those by hand.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from intake_bot.catalog import Catalog  # noqa: E402

ROW = re.compile(r"^\s*\[(.+)\],?\s*(?://.*)?$")


def php_rows(path: Path) -> list[list]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = ROW.match(line)
        if not m or "=>" in m.group(1):
            continue
        rows.append(eval(m.group(1).replace("null", "None").replace("\\'", "'"), {}, {}))  # literal arrays only
    return rows


def sale_names(path: Path) -> list[str]:
    block = re.search(r"\$saleOnly\s*=\s*\[(.*?)\];", path.read_text(encoding="utf-8"), re.S)
    return re.findall(r"'([^']+)'", block.group(1)) if block else []


def main(repo: Path) -> int:
    seeders = repo / "database" / "seeders"
    c = Catalog.load()
    problems: list[str] = []

    def compare(label: str, theirs: dict, ours: dict) -> None:
        for name in sorted(theirs.keys() - ours.keys()):
            problems.append(f"{label}: {name!r} is in the accounting seeder but not in config")
        for name in sorted(ours.keys() - theirs.keys()):
            problems.append(f"{label}: {name!r} is in config but not in the accounting seeder")
        for name in sorted(theirs.keys() & ours.keys()):
            if theirs[name] != ours[name]:
                problems.append(f"{label}: {name!r} differs — accounting {theirs[name]}, config {ours[name]}")

    compare("strap type", {r[0]: (r[1],) for r in php_rows(seeders / "StrapTypeSeeder.php")},
            {i.name: (i.code,) for i in c.strap_types.items})
    compare("color", {r[0]: (r[1], r[2]) for r in php_rows(seeders / "ColorSeeder.php")},
            {i.name: (i.code, i.number) for i in c.colors.items})
    compare("index type", {r[0]: (r[1],) for r in php_rows(seeders / "IndexTypeSeeder.php")},
            {i.name: (i.code,) for i in c.index_types.items})

    brand_rows = php_rows(seeders / "BrandSeeder.php")
    theirs = {}
    for r in brand_rows:
        if isinstance(r[2], list):  # list scheme: name, code, discounts, ...
            theirs[r[0]] = (r[1], "list", tuple(round(d, 4) for d in r[2]), None)
        else:                        # nett scheme: name, code, markup, ...
            theirs[r[0]] = (r[1], "nett", (), round(r[2], 4))
    for name in sale_names(seeders / "BrandSeeder.php"):
        theirs[name] = (None, "sale", (), None)
    ours = {b.name: (b.code, b.scheme, tuple(round(d, 4) for d in b.purchase_discounts),
                     round(b.markup, 4) if b.markup is not None else None) for b in c.brands.items}
    compare("brand (code, scheme, discounts, markup)", theirs, ours)

    if problems:
        print("\n".join(problems))
        return 1
    print(f"OK — {len(ours)} brands, {len(c.strap_types.items)} strap types, "
          f"{len(c.colors.items)} colors, {len(c.index_types.items)} index types match.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(Path(sys.argv[1])))
