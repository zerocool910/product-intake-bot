# Accounting import columns (mirror)

**Source of truth:** `globaltimecollection-accounting/docs/product-import-template.md`
and `app/Services/ProductImportService.php` (checked at `830ee94`)
(the `/products-import` Excel importer). This is a copy so this repo is
self-contained. If the accounting side changes, update this file,
`IMPORT_COLUMNS` in `intake_bot/item.py`, and `review()` in the same file.

One row per product; the first row is headers (case-insensitive, spaces or
underscores both work). The accounting app generates the printed name, SKU,
and cost/selling price from these columns. They are not typed in directly.

| Column | Required | Notes |
|---|---|---|
| `type` | Always | `watch` or `clock`. |
| `brand` | Always | Must match a brand already in Brands (case-insensitive). |
| `strap_type` | Watch only | Must match a Strap Type name. |
| `strap_color` | Watch only | Must match a Color name. |
| `case_color` | Always | Must match a Color name. |
| `gender` | Watch only | `G`/`L`/`A`/`G/L` or `Gents`/`Ladies`/`All`/`Gents/Ladies`. |
| `dial_color` | No | Color name. Start of the Unique Descriptor. |
| `index_type` | No | Index Type name. Only if `dial_color` is given. |
| `crown_color` | No | Color name. Only if `index_type` is given. |
| `ref_code` | Only for AC/Expedition/Christophe Arden | Owner-invented reference number (e.g. `AE1200`). |
| `manufacturer_sku` | Every other brand | Used directly as `Kode Barang`, e.g. `SRPD53K1`. |
| `supplied_price` | Unless the brand is SALE-only | HLB for a List-scheme brand, HBB for a Nett-scheme brand. |
| `selling_price` | SALE-only brands only | No formula, set by hand. |
| `cost_price` | No | SALE-only brands; optional unit cost if `quantity` seeds opening stock. |
| `quantity` | No | Opening stock quantity (default 0), recorded as a real Purchase. |
| `category` | No | Must match a Product Category name if given. |

Rows are imported independently: a bad row is skipped and reported, and
doesn't block the rest.

**SKUs must be new** (since `dc919ae`): a row is rejected if its SKU (the
manufacturer SKU, or the generated AC/EXP/CA SKU) already exists in
accounting or appears earlier in the same file. Restocks of existing items
are recorded as stock receipts in accounting, not imported again.

Known gaps on the accounting side (as of 2026-11-04): AC/EXP/CA **clocks**
have no generated-SKU pattern yet, so they need `manufacturer_sku` set by
hand. Barcode is not an import column yet.

## What this bot adds

The staging sheet has these columns **after** the import columns, for
audit only: `intake_id`, `logged_by`, `logged_at`, `photos`,
`preview_name`, `preview_sku`, `notes`. The importer ignores them: it
reads each field by its header name (lowercased, spaces → underscores).
It only reads the workbook's **active** sheet, so make sure the `Intake`
tab is the selected one when downloading.
