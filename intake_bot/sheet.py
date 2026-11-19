"""Google Sheets staging sheet: one row per confirmed item.

The first len(IMPORT_COLUMNS) columns are exactly the accounting importer's
template; the audit columns after them are for people (who logged it, which
photo). Export or download the sheet as .xlsx for /products-import.
"""

from __future__ import annotations

import logging

import gspread

from .item import AUDIT_COLUMNS, IMPORT_COLUMNS

log = logging.getLogger(__name__)
HEADER = IMPORT_COLUMNS + AUDIT_COLUMNS


class Sheet:
    def __init__(self, credentials_file: str, spreadsheet_id: str, worksheet: str):
        client = gspread.service_account(filename=credentials_file)
        book = client.open_by_key(spreadsheet_id)
        try:
            self.ws = book.worksheet(worksheet)
        except gspread.WorksheetNotFound:
            self.ws = book.add_worksheet(title=worksheet, rows=1000, cols=len(HEADER))
        self._ensure_header()

    def _ensure_header(self) -> None:
        first = self.ws.row_values(1)
        if not first:
            self.ws.append_row(HEADER, value_input_option="RAW")
        elif first[: len(HEADER)] != HEADER:
            raise RuntimeError(
                f"Worksheet {self.ws.title!r} has an unexpected header row. "
                f"Expected: {HEADER}. Fix the sheet or point SHEET_WORKSHEET at a new tab.")

    def append(self, row: dict[str, object]) -> None:
        self.ws.append_row([row.get(c, "") for c in HEADER], value_input_option="RAW")

    def same_sku(self, sku: str) -> list[dict]:
        """Rows already in the sheet with this SKU. The importer rejects a SKU
        that repeats within one file or already exists in accounting."""
        if not sku:
            return []
        return [r for r in self.ws.get_all_records(expected_headers=HEADER)
                if str(r.get("preview_sku", "")).strip().upper() == sku.strip().upper()]

    def similar(self, row: dict[str, object]) -> list[dict]:
        """Rows already in the sheet that would print the same name: same brand,
        same SKU / ref code, same strap + strap color + case (+ descriptor).

        Since names include the SKU / ref code, this is mostly AC/EXP/CA models
        sharing a ref code; one of them needs a Unique Descriptor (dial → index
        → crown). Only checks this sheet, not the accounting catalog.
        """
        keys = ("type", "brand", "ref_code", "manufacturer_sku", "strap_type", "strap_color", "case_color",
                "dial_color", "index_type", "crown_color")
        found = []
        for existing in self.ws.get_all_records(expected_headers=HEADER):
            if all(str(existing.get(k, "")).lower() == str(row.get(k, "")).lower() for k in keys):
                found.append(existing)
        return found
