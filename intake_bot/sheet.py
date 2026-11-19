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
        """Rows already in the sheet with this SKU, each with its sheet row
        number in "_row". The importer rejects a SKU that repeats within one
        file, so a new item with the same SKU replaces the old row instead."""
        if not sku:
            return []
        want = sku.strip().upper()
        return [{**r, "_row": i + 2}  # +1 for the header row, +1 for 1-based rows
                for i, r in enumerate(self.ws.get_all_records(expected_headers=HEADER))
                if str(r.get("preview_sku", "")).strip().upper() == want]

    def save(self, row: dict[str, object]) -> list[str]:
        """Write a confirmed row. If its SKU is already in the sheet, overwrite
        that row (and clear any further copies) instead of adding a duplicate.
        Looks the SKU up again at write time, so it overwrites the right row
        even if the sheet changed since the summary was shown.
        Returns the intake_ids that were replaced ([] if the row was added)."""
        values = [row.get(c, "") for c in HEADER]
        matches = self.same_sku(str(row.get("preview_sku", "")))
        if not matches:
            self.ws.append_row(values, value_input_option="RAW")
            return []
        first, *extra = matches
        self.ws.update(values=[values], range_name=f"A{first['_row']}", value_input_option="RAW")
        for m in extra:  # shouldn't happen, but never leave a second copy behind
            self.ws.update(values=[[""] * len(HEADER)], range_name=f"A{m['_row']}", value_input_option="RAW")
        return [str(m.get("intake_id", "")) for m in matches]

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
