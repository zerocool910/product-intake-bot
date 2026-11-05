# Product Intake Bot — spec (v1)

Revised from the v0 draft after checking it against the brand pricing
reference, the naming convention, and the importer that
`globaltimecollection-accounting` actually built for `#1`.

## Boundary

A separate codebase with its own deploy and no code shared with the accounting
app. **Its only output is rows in the accounting importer's column shape**
(`docs/accounting-import-columns.md`), staged in a Google Sheet and exported
to `.xlsx` for `/products-import`. It never writes to the accounting
database.

Because the importer generates the name, SKU, cipher and prices itself, the
bot only has to supply **raw attributes plus one price number**. The name /
SKU / HBB / HJB the bot shows are a **preview** so staff can see exactly
what will be printed before confirming. The accounting app stays the
source of truth.

## Design rule

Claude only picks values from closed lists (brand, strap type, colors,
index type, watch/clock) and copies printed text (manufacturer SKU, tag
cipher letters). Everything derived (name, SKU, cipher, HBB/HJB) is plain
code (`intake_bot/naming.py`, `intake_bot/pricing.py`) and is tested against the
verified examples (AC 1007LS → HBB 453,150; HJB 966,000 → EBB;
`ACAE1200SS18C18G`).

## Flow

1. Staff send a photo, or an album (item + tag + box), with a caption:
   brand, **the one price number**, gender, optionally `ref AE1200`,
   `qty 2`, `sku SRPD53K1`.
2. The bot saves the photos to disk (`data/photos/<intake_id>_<n>.jpg`).
   Telegram's file URLs expire.
3. Claude (vision) proposes: type, brand, strap type/color, case color,
   manufacturer SKU if printed, and notes (dial color, index style, and
   any tag-cipher letters). Every field it isn't sure of goes in
   `uncertain` and shows ❓.
4. Caption values override Claude's. Gender, prices, quantity, ref code
   and category are **never** taken from the photo.
5. The bot replies with a summary: fields, HBB/HJB, preview name and SKU,
   what's still missing, a tag-cipher cross-check (if the tag letters
   decode to a different HJB than the typed price gives, it warns), and a
   duplicate-name warning if an existing sheet row would print the same
   name (meaning a Unique Descriptor is needed).
6. Staff reply in plain words to correct ("rubber not leather, ladies,
   dial blue"). Claude applies the change to the fields and the summary
   refreshes.
7. **✅ Confirm** is only offered when the row would pass the importer's
   required-column rules. Confirm appends the row; ❌ Cancel discards it.
8. Periodically, someone exports the sheet tab to `.xlsx` and uploads it
   to `/products-import`.

## Decisions (answers to the v0 open questions)

| v0 question | Decision |
|---|---|
| Where does HBB/cost come from? | **Typed in the caption.** It's one number whose meaning depends on the brand's scheme: List brands → HLB (distributor list price); Nett brands → HBB (cost); SALE brands → the selling price. This is exactly the importer's `supplied_price` / `selling_price`. |
| Single or multi-staff? | **Multi-staff with an allowlist** (`ALLOWED_USER_IDS`). Each row records `logged_by` (Telegram name + ID). Unknown users get their ID back so the owner can add them. |
| Hosting | **An always-on VPS with long-polling**, running as a systemd service. No webhook means no domain or HTTPS needed. (The shared hosting the accounting app runs on usually can't keep a process alive.) |
| Tech stack | **Python 3.11+**, matching the Seiko Content Aggregator. python-telegram-bot, anthropic SDK (structured outputs), gspread, SQLite. |
| Repo | `zerocool910/product-intake-bot`. The target row shape is now fixed by the importer. |

Also settled from the accounting session:
- The price cipher **truncates** to thousands (`intdiv`), it doesn't round.
- `NAAE1200SS18C18G` used a placeholder brand code "NA". The real form is
  `{BrandCode}{RefCode}...`, e.g. `ACAE1200SS18C18G`. Ref codes can contain
  letters.
- `type` (watch/clock) is an explicit column. Seiko JD defaults to clock.

## Non-goals (v1)

- Writing to the accounting database or API.
- Inferring price or gender from the photo.
- Retail (DJE) / wholesale (DJG) prices. DJE is a range and Nett wholesale
  is unconfirmed, so the accounting side owns these.
- Printing new tags.
- Checking the **accounting catalog** for duplicates. The duplicate-name
  check only covers rows already in the staging sheet.

## Open items

1. ~~Brand names vs the accounting `BrandSeeder`~~ **Checked 2026-11-04**
   (accounting `71f0985`). All 29 brands, 13 strap types, 23 colors and
   3 index types match, with codes and rates. The smartwatch brands are
   named `Smartwatch (non-Huawei)` and `Smartwatch (Huawei)`, and SALE
   brands have no code. To re-check after the accounting seeders change:
   `python scripts/check_accounting_seeders.py <path-to-accounting-repo>`.
2. ~~Which brands print a code vs the full name~~ **Resolved on the
   accounting side (`830ee94`).** Brands have a `print_code_on_tag` flag
   (default true). Seiko, Seiko JD and Casio are set to false, so they
   print their full name, and the owner can flip any brand on the
   accounting `/brands` screen. `config/brands.yaml` mirrors the flag.
   **A change made on `/brands` must be copied into `config/brands.yaml`
   by hand**, because the seeder checker can't see UI edits.
   - **Bug this exposed (accounting side, not fixed yet):** the clock
     pattern is `{Brand} JD Bd …`, so Seiko JD printing its full name gives
     **`Seiko JD JD Bd SIL - SOO`**. The fix belongs in the accounting app,
     either in `buildClockName()` or by deciding that Seiko JD prints
     `Seiko` on clocks. The bot preview currently shows the doubled "JD",
     matching what the importer would produce (pinned in
     `test_clock_name_has_no_strap_or_gender`), and should change when the
     accounting fix lands.
3. **Ref-code registry for AC/EXP/CA.** Staff must type the owner-assigned
   number. A later version could look up "next free number" or match a
   known model.
4. ~~Does the importer ignore the extra audit columns?~~ **Yes.** The
   importer reads each field by header name and ignores other columns. It
   only reads the workbook's **active** sheet, though, so keep the
   `Intake` tab first and selected when downloading the `.xlsx`.
5. **Nett-scheme HJB rounding.** HBB × 1.5 is rarely a round figure.
   There is no rounding rule, and the cipher just truncates.
6. **Photo backup / off-box storage.** Photos live on the VPS disk under
   `data/`. Back that folder up, or add cloud storage later.
7. **Restocks.** Since `dc919ae` the importer rejects a SKU that already
   exists in accounting or repeats within the file. The bot warns when a
   SKU is already in the staging sheet, but it can't see the accounting
   catalog. More stock of an item that's already in accounting is a stock
   receipt there, not a new import row.
8. **Direct sheet → importer path** (skipping the `.xlsx` export), if `#1`
   ever reads Google Sheets.
