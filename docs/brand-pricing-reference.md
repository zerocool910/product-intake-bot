> Mirror of `globaltimecollection-accounting/docs/brand-pricing-reference.md` at `4634f6b`. The accounting repo is the source of truth; re-copy it when it changes.

# Brand pricing reference

Source: `Daftar_Harga_per_Merk_20250906_v1.xlsx` (sheet `DAFTAR HARGA`, last
updated 20260113) and `Skema Harga`, provided by the owner. This is the
mapping Layer 6's product-entry form should use to compute `cost_price` and
`selling_price` from whatever number the supplier/distributor actually gave
for a brand, instead of those being typed in by hand per product.

Two pricing schemes, selected per brand. `HBB` = Harga Beli Barang (our
cost). `HLB` = Harga List Barang (distributor's list price). `HJB` = Harga
Jual Barang (our list/reference selling price — in both schemes this equals
`HLB`, so there is effectively one "list" number per item, not two).

Verified against a real product: **AC 1007LS BLACK**, HJB 950,000, Scheme
LS, DB 47%+10% → `950,000 × (1-0.47) × (1-0.10) = 453,150`, which is
exactly the Harga Beli Accurate shows for that product.

## Scheme "List" (LS)

Distributor publishes a list price; our cost is a discount off it.

```
HBB = HLB × (1 − DB₁) × (1 − DB₂) × …   (sequential, not additive, when DB is compound)
HJB = HLB
Harga Jual Eceran (retail)   = HJB × (1 − DJE)      [unless noted otherwise]
Harga Jual Grosir (wholesale) = HJB × (1 − DJG)
```

| Brand | Code | DB (cost disc. off HLB) | DJE (retail disc. off HJB) | DJG (wholesale disc. off HJB) |
|---|---|---|---|---|
| Alexandre Christie | AC | 47%+10% | 20–40% | 45–47% |
| Expedition | EXP | 50%+10% | 25–40% | 50% |
| Seiko (Watch + Clock) | SK | 30%+3%+2% | 10–15% | 30% |
| Alba | AL | **30%+3%+2% (corrected — matches Seiko Watch, not the original 10%+3%+2%)** | **HJB +30% first, then −DJE** (not a plain discount off HJB) | 10% |
| Aigner | AIG | 40% | 20–30% | 35% |
| Casio | CS | 42% | 20–30% | 40% |
| Guess | GS | 35% | 15–25% | 30% |
| Christophe Arden | CA | 50%+10% | 30–45% | 47–50% |

## Scheme "Nett" (N)

Distributor gives cost directly (no published list price); we derive our
own list price by marking it up.

```
HLB = HBB × (1 + UP)
HJB = HLB
Harga Jual Eceran (retail) = HJB × (1 − DJE)
Harga Jual Grosir (wholesale) = HBB × 1.05, or an owner override (see note below)
```

| Brand | Code | UP (markup off HBB) | DJE (retail disc. off HJB) |
|---|---|---|---|
| Bonia | BN | 50% | 20–30% |
| Timberland | TBL | 50% | 20–30% |
| Sezen | SZ | 50% | 20–30% |
| Digitec | DG | 50% | 20–30% |
| Jean Michelle | JM | 50% | 20–30% |
| Lobor | LB | 50% | 20–30% |
| Hush Puppies | HP | 50% | 20–30% |
| Caterpillar | CAT | 50% | 20–30% |
| Condotti | CN | 50% | 20–30% |
| Wishkey | WK | 50% | 20–30% |
| Mirage | MRG | 50% | 20–30% |
| Smartwatch (non-Huawei) | SM | 70% | 30–50% |
| Smartwatch (Huawei) | — | 50% | 30–40% |

**Unconfirmed:** the wholesale column for Nett-scheme brands shows
`HBB + 5% OR HBB + DOO` (most brands) or `HBB + 5% OR HBB + UM` / `+ MO`
(Smartwatch lines). `DOO`/`UM`/`MO` aren't spelled out anywhere in the
source file — read as some kind of owner-discretion override on top of the
flat +5%, but not confident enough to encode as a formula. Treat wholesale
pricing for these brands as "+5% over cost, or owner sets it manually" until
confirmed.

## "SALE" brands — no formula, clearance terms

Police, Alain Delon, Hummer, Charles Jourdan, Festina, Christ Verra, Lee
Cooper: both retail and wholesale columns just say `SALE` — ad-hoc pricing,
not formula-driven. Sold under fixed clearance terms (Ketentuan SALE, from
the same sheet):

1. Tidak ada garansi (no warranty).
2. Tidak bisa tukar barang (no exchange).
3. Kulit bisa diganti tapi kena charge; baterai diganti baru (strap
   replacement chargeable, battery always replaced new).

## A secondary, older markdown schedule (not current — context only)

A separate, older tab in the same source area (`Sheet1`, last updated 26
Jan 2024) shows a *different* axis: an "Opening" vs "Closing" discount per
brand that increases as stock ages — e.g. Aigner splits into New/Regular/Old
model with escalating discount (15–20% / 20–25% / 25–35%). This predates
`DAFTAR HARGA` and isn't the live pricing matrix, but the underlying idea —
discount schedule tied to how long a SKU has sat in stock — is a real,
separate feature (automated stale-stock markdown) worth considering later,
not part of the brand markup mapping above.

## What this doesn't cover yet

- Brand-level pricing parameters (scheme, DB/UP, DJE, DJG) currently have
  nowhere to live — `Product.brand` is a plain string. For this reference
  to actually drive product entry instead of being a lookup table a human
  consults, `Brand` likely needs to become a real model carrying these
  rates, with `Product` referencing it.
- The owner-discretion override codes (`DOO`/`UOO`/`AOO`/`UM`/`MO`) that
  appear across both pricing sheets.
- ~~Seiko JD (Clock)'s scheme/rate~~ — resolved: Seiko Watch, Seiko JD, and
  Alba all use the same DB (30%+3%+2%, List scheme). The `master` sheet's
  `UP2` tag on Seiko JD was outdated.
- ~~Seiko JD (Clock) as its own Brand, with different DJE/DJG than Seiko
  Watch~~ — retired by owner decision: Seiko JD is no longer a separate
  Brand at all. A Seiko clock uses the plain "Seiko" brand/pricing above;
  "JD" is now a fixed literal in the Clock name pattern (see
  docs/product-naming-convention.md), not a brand distinction. The
  different wholesale/retail numbers this row used to carry are dropped,
  not preserved elsewhere.
