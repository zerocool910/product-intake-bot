> Mirror of `globaltimecollection-accounting/docs/product-naming-convention.md` at `a5d8fbe`. The accounting repo is the source of truth; re-copy it when it changes.

# Product naming & coding convention

Reverse-engineered from `Upload_Barang_List_v0.1.6.xlsx`'s live Excel
formulas, then corrected and extended directly by the owner. This is the
convention `Brand`/`Product` creation in the accounting app should
implement — the goal is to generate the same tag text and SKU a human
currently works out by hand with a calculator and this spreadsheet.

Two independent coding systems feed into it: a **price cipher** (hides the
price from customers on a printed tag) and a set of **attribute codes**
(build the item's name and, for some brands, its SKU).

## Price cipher

Each digit maps to a letter:

| 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|
| O | D | U | T | A | M | B | S | C | E |

Take the item's computed selling price (HJB), drop the last 3 digits
(state it in thousands of Rupiah), substitute each remaining digit through
the table, concatenate. Verified: HJB 966,000 → "966" → 9=E, 6=B, 6=B →
**EBB**. This is what's on the tags in the owner's photos — readable by
staff who know the cipher, meaningless to a customer.

## Attribute codes

**Gender**: Gents=G, Ladies=L, All=A, Gents/Ladies=G/L

**Strap Type**: Leather=LS, Rubber=RB, Stainless St.=SS, Steel=SL, Rantai
Kombi=DT, Stainless St. Mica=SM, Stainless St. Ceramic=SC, Ceramic=CR,
Rantai Pasir=MS, Kanvas=NS, Carbon Fibre=CF, Titanium St.=TS, Sailcloth=SO.
Resolved — Steel keeps SL, Stainless St. Ceramic keeps SC, Sailcloth gets
its own free code, SO.

**Color** — two parallel schemes, deliberately, not a leftover
inconsistency: the **printed/display name always uses the 3-letter code**
below; the **AC/EXP/CA invented reference code (below) always uses the
old 2-digit numeric code**. Confirmed as two genuinely separate uses, not
one scheme that should be unified. Note the *current* live Accurate
product names spell colors out in full, e.g. "BLACK" not "BLK" — the
3-letter scheme applies to new items from here on, not a retroactive
rename.

3-letter codes (for names):

| Color | Code | Color | Code |
|---|---|---|---|
| White | WHT | Cream | CRM |
| Black | BLK | Beige | BEI |
| Red | RED | Khaki | KHK |
| Blue | BLU | Pink | PNK |
| Navy Blue | NVY | Silver | SIL |
| Yellow | YLW | Silver Stone | SLS |
| Green | GRN | Rose Gold | RSG |
| Orange | ORG | Gold | GLD |
| Purple | PPL | Titanium | TTN |
| Grey | GRY | Colored | CLR |
| Brown | BRN | | |
| Dark Grey | DGY | | |
| Ivory | IVR | | |

2-digit numeric codes (for the AC/EXP/CA invented reference code only —
from the original `coding` sheet):

| Color | Code | Color | Code |
|---|---|---|---|
| White | 01 | Pink | 17 |
| Black | 02 | Silver | 18 |
| Red | 03 | Silver Stone | 19 |
| Blue | 04 | Rose Gold | 20 |
| Navy Blue | 05 | Gold | 21 |
| Yellow | 06 | Titanium | 22 |
| Green | 07 | Colored | 23 |
| Orange | 08 | | |
| Purple | 09 | | |
| Grey | 10 | | |
| Brown | 11 | | |
| Dark Grey | 12 | | |
| Ivory | 13 | | |
| Cream | 14 | | |
| Beige | 15 | | |
| Khaki | 16 | | |

**Index type** (used only inside Unique Descriptor, below): Numeral=NUM,
Baton=BTN, Roman=ROM, Arabic=ARB. Confirmed non-exhaustive ("there are
others, but those three should be sufficient as per past experience") —
kept as an open/extensible list in the schema, not a fixed enum, so a
new style doesn't need a migration to add; Arabic (owner update
2026-11-25, from the old "Idx Arab" naming) is the first of those
additions — a data change via `IndexTypeSeeder`, not a schema one.

## Brand code

The short form of the brand name — exists purely because long names
(Alexandre Christie, Expedition, Christophe Arden) don't fit well on an
80mm receipt. Short names (Casio, Seiko) print directly with no separate
code; there's nothing to look up, not a data gap.

## Reference number / SKU

- **Alexandre Christie, Expedition, Christophe Arden (AC/EXP/CA)**: the
  supplier (Toko Jam Duta Mas) provides no definitive SKU for these three
  brands, so the owner invents and assigns a reference number per distinct
  model themselves (the "1007", "1031", "6757" seen on the physical tags).
- **Every other brand** (Seiko, Alba, Casio, etc.): the manufacturer's own
  SKU is used directly (e.g. `SRPD53K1`) — it already encodes distinct
  color/model/shape, so no self-invented number is needed.

## Name patterns

**Watch** (owner update 2026-11-19, `{SKU}` added):
```
{Brand} {SKU} {Strap Type Code} {Strap Color Code} Bd {Case Color Code} {Unique Descriptor}. {Gender} - {Price Cipher Code}
```
`{SKU}` is the manufacturer SKU (e.g. `SRPD53K1`), or for AC/EXP/CA the
owner's ref code (e.g. `1007`) — the same rule as the Clock pattern
below, **not** the long built `Kode Barang` (see **SKU / `Kode Barang`
generation** below). Example: `AC 1007 SS BLK Bd BLK. G - EMO`. "Bd" =
short for *Bodi* (slang for Case/Body) — a fixed, permanent token, not
a placeholder.

**Clock**:
```
{Brand} JD {SKU / Ref Code} Bd {Case Color Code} - {Price Cipher Code}
```
No strap fields (clocks don't have straps), no Gender, no Unique
Descriptor shown in what's been confirmed so far. "JD" is a fixed,
permanent token like "Bd" — not derived from the brand's name. Seiko
clocks (formerly modeled as a separate "Seiko JD" brand/pricing) are
just brand "Seiko"; "JD" supplies the clock marker, so Seiko isn't typed
in twice.

~~Confirmed: for the **Watch** pattern, the AC/EXP/CA self-invented
reference number does not appear in the printed name at all — it lives
only in the internal SKU/`Kode Barang` field. The Clock pattern above is
the one exception.~~ Superseded 2026-11-19: both patterns now print the
SKU/ref code — the owner wanted the same `{SKU}` treatment extended to
Watch, not kept Clock-only.

## Unique Descriptor

Used only when Strap Type + Strap Color + Case Color are all identical
between two items that are still genuinely different models — e.g. same
stainless bracelet, same silver strap, same silver case, but one has a
numeral index and the other baton.

**In the printed name** (3-letter color codes, hyphen-separated):
```
di{Color Code}-idx{Index Code}-cr{Color Code}
```

**Cumulative, not either/or** — fields are checked and *added* in this
fixed order (dial → index → crown) until the items are actually
distinguished, not replaced as you go:
1. Try dial color alone (`di{Code}`). If that's enough to tell the two
   apart, stop there.
2. If dial color is *also* identical, append the index descriptor
   (`di{Code}-idx{Code}`) — dial still shown even though it isn't the
   actual differentiator.
3. If still identical, append crown color too
   (`di{Code}-idx{Code}-cr{Code}`).

**In the AC/EXP/CA reference code** — same structure, same cumulative
logic, but **no hyphens** and **2-digit numeric color codes** instead of
3-letter: `di{Color Code Number}idx{Index Code}cr{Color Code Number}`.
Example: a name-level descriptor of `diBLU-idxNUM-crSIL` becomes
`di04idxNUMcr18` when appended to the reference code (Blue=04, Numeral has
no numeric table so stays `NUM` literally, Silver=18).

## SKU / `Kode Barang` generation

- **Other brands** (definitive manufacturer SKU exists): `Kode Barang` =
  the manufacturer SKU directly, no construction needed.
- **AC/EXP/CA**: `{BrandCode}{RefCode}{StrapTypeCode}{StrapColorCode
  (numeric)}C{CaseColorCode (numeric)}{GenderCode}`, with the Unique
  Descriptor (in its no-hyphen, numeric-color form above) appended at the
  end when needed to keep two otherwise-identical items' SKUs distinct.
  Verified base-case example (no Unique Descriptor needed):
  `NAAE1200SS18C18G`.
