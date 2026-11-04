# CLAUDE.md

Telegram bot that turns product photos into rows for the accounting app's
`/products-import`. Read `docs/product-intake-bot-spec.md` first.

- **Boundary:** output is only rows in the column shape in
  `docs/accounting-import-columns.md`. Never talk to the accounting
  database. That file mirrors `globaltimecollection-accounting/docs/product-import-template.md`.
  When that changes, update the mirror, `IMPORT_COLUMNS`, and `review()` in
  `intake_bot/item.py` together.
- **Claude picks from lists, code derives.** Names, SKUs, cipher and prices
  are computed in `naming.py` / `pricing.py`, never by the model. Gender,
  prices, quantity and ref code never come from the photo.
- **Names must match the accounting lookup tables** (the importer matches
  by name). Codes and rates live in `config/*.yaml`, not in code, and
  mirror the accounting seeders. Check with
  `scripts/check_accounting_seeders.py <accounting repo>`.
- Every rule in `docs/product-naming-convention.md` or
  `docs/brand-pricing-reference.md` that the code implements has a test in
  `tests/test_rules.py` using the doc's verified example. Run `python -m pytest`.
- Deployed on an always-on VPS as a systemd service with long-polling. No
  webhook.
