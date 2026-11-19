"""Google Sheets staging sheet: one row per confirmed item.

The first len(IMPORT_COLUMNS) columns are exactly the accounting importer's
template; the audit columns after them are for people (who logged it, which
photo). Export or download the sheet as .xlsx for /products-import.
"""

from __future__ import annotations

import logging

import gspread
from gspread.utils import rowcol_to_a1

from .item import AUDIT_COLUMNS, IMPORT_COLUMNS, LEGEND

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
        self.book = book
        self._ensure_header()

    def _ensure_header(self) -> None:
        if self.ws.col_count < len(HEADER):
            self.ws.add_cols(len(HEADER) - self.ws.col_count)
        first = self.ws.row_values(1)
        if not first:
            self.ws.update(values=[HEADER], range_name="A1", value_input_option="RAW")
        elif first == HEADER[: len(first)] and len(first) < len(HEADER):
            # Older header (fewer audit columns): extend it in place.
            self.ws.update(values=[HEADER], range_name="A1", value_input_option="RAW")
            log.info("Added columns %s to %r", HEADER[len(first):], self.ws.title)
        elif first[: len(HEADER)] != HEADER:
            raise RuntimeError(
                f"Worksheet {self.ws.title!r} has an unexpected header row. "
                f"Expected: {HEADER}. Fix the sheet or point SHEET_WORKSHEET at a new tab.")

    def write_guide(self, samples: list[tuple[str, dict]]) -> None:
        """Hover notes on the Intake header cells, and a Legend tab explaining
        every column with worked sample rows. Rewritten at every start; never
        touches the data rows. The importer only reads the active tab, so the
        Legend tab doesn't affect imports as long as Intake is selected."""
        self.ws.update_notes({rowcol_to_a1(1, n): self._note(col) for n, col in enumerate(HEADER, start=1)})
        self.ws.freeze(rows=1)

        title = "Legend"
        try:
            guide = self.book.worksheet(title)
            guide.clear()
        except gspread.WorksheetNotFound:
            guide = self.book.add_worksheet(title=title, rows=100, cols=len(HEADER) + 1)
        rows: list[list] = [
            [f"How to read the {self.ws.title!r} tab. Written by the bot; edits here are overwritten."],
            ["Download the .xlsx with the Intake tab selected: the importer reads only the active tab, "
             "and only the import columns (the bot's columns after them are ignored)."],
            [],
            ["Column", "Used by import?", "Required", "Filled by", "Meaning"],
        ]
        for col in HEADER:
            filled_by, required, meaning, imported = LEGEND[col]
            rows.append([col, "Yes" if imported else "No (bot info)", required, filled_by, meaning])
        rows += [[], ["Sample output (what the bot writes for these cases)"], ["Case"] + HEADER]
        rows += [[label] + [row.get(c, "") for c in HEADER] for label, row in samples]
        guide.update(values=rows, range_name="A1", value_input_option="RAW")
        guide.format("A4:E4", {"textFormat": {"bold": True}})
        guide.format(f"A{len(HEADER) + 7}:{rowcol_to_a1(len(HEADER) + 7, len(HEADER) + 1)}", {"textFormat": {"bold": True}})

    @staticmethod
    def _note(col: str) -> str:
        filled_by, required, meaning, imported = LEGEND[col]
        req = f"Required: {required}. " if required else ""
        kind = "" if imported else "Bot info (not imported). "
        return f"{kind}{req}Filled by: {filled_by}.\n{meaning}"

    def append(self, row: dict[str, object]) -> None:
        self.ws.append_row([row.get(c, "") for c in HEADER], value_input_option="RAW")

    def same_sku(self, sku: str) -> list[dict]:
        """Rows already in the sheet with this SKU (earlier receipts), each with
        its sheet row number in "_row"."""
        if not sku:
            return []
        want = sku.strip().upper()
        return [{**r, "_row": i + 2}  # +1 for the header row, +1 for 1-based rows
                for i, r in enumerate(self.ws.get_all_records(expected_headers=HEADER))
                if str(r.get("preview_sku", "")).strip().upper() == want]

    def save(self, row: dict[str, object]) -> None:
        """Append a confirmed row. Every confirmation is its own receipt line,
        even when the SKU was received before (owner decision 2026-11-19: the
        bot is goods receiving, so two deliveries are two rows)."""
        self.ws.append_row([row.get(c, "") for c in HEADER], value_input_option="RAW")

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
