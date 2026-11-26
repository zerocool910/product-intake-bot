"""Google Sheets staging sheet: one row per goods receipt, one tab per month.

Rows go to the tab for the current month, e.g. "Intake 2026-11" (owner
decision 2026-11-19). When the month changes the bot starts a new tab and the
previous month's tab stays as the record. Every row carries its receipt_date.
Within the same date a repeat SKU is either replaced or has its qty added
(staff choose); a later date is simply a new receipt.

The first len(IMPORT_COLUMNS) columns are exactly the accounting importer's
template; the bot's own columns come after them. Download a month tab as
.xlsx (with that tab selected) for /products-import.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

import gspread
from gspread.utils import rowcol_to_a1

from .item import AUDIT_COLUMNS, IMPORT_COLUMNS, LEGEND

log = logging.getLogger(__name__)
HEADER = IMPORT_COLUMNS + AUDIT_COLUMNS
QTY_COL = HEADER.index("quantity") + 1
NOTES_COL = HEADER.index("notes") + 1


class Sheet:
    def __init__(self, credentials_file: str, spreadsheet_id: str, prefix: str, timezone: str):
        client = gspread.service_account(filename=credentials_file)
        self.book = client.open_by_key(spreadsheet_id)
        self.prefix = prefix
        self.tz = ZoneInfo(timezone)
        self._tabs: dict[str, gspread.Worksheet] = {}
        self.current()  # fail at startup, not on the first photo, if the sheet is unusable

    # ---------- dates and tabs ----------

    def today(self) -> date:
        return datetime.now(self.tz).date()

    def fresh_tab(self, label: str | None = None) -> tuple[str, str]:
        """Start an empty tab for this month now: the current one is renamed
        (kept as is, and out of the bot's same-day / duplicate checks) and a
        new "<prefix> YYYY-MM" tab is created. Returns (old new name, tab)."""
        ws = self.current()
        base = label.strip() if label and label.strip() else f"{ws.title} (old {datetime.now(self.tz):%d-%m %H:%M})"
        taken = {w.title for w in self.book.worksheets()}
        name, n = base, 2
        while name in taken:
            name, n = f"{base} {n}", n + 1
        ws.update_title(name)
        self._tabs = {}
        return name, self.current().title

    def tab_url(self) -> str | None:
        """Link that opens the spreadsheet on this month's tab."""
        try:
            return f"https://docs.google.com/spreadsheets/d/{self.book.id}/edit#gid={self.current().id}"
        except Exception:
            return None

    def tab_title(self, day: date | None = None) -> str:
        return f"{self.prefix} {(day or self.today()):%Y-%m}"

    def current(self) -> gspread.Worksheet:
        """This month's tab, created (with header and notes) when the month changes."""
        title = self.tab_title()
        ws = self._tabs.get(title)
        if ws is None:
            try:
                ws = self.book.worksheet(title)
            except gspread.WorksheetNotFound:
                ws = self.book.add_worksheet(title=title, rows=1000, cols=len(HEADER))
                log.info("Started a new month tab %r", title)
            self._ensure_header(ws)
            self._header_notes(ws)
            self._tabs = {title: ws}  # drop last month's handle
        return ws

    @staticmethod
    def _ensure_header(ws: gspread.Worksheet) -> None:
        if ws.col_count < len(HEADER):
            ws.add_cols(len(HEADER) - ws.col_count)
        first = ws.row_values(1)
        if not first:
            ws.update(values=[HEADER], range_name="A1", value_input_option="RAW")
        elif first == HEADER[: len(first)] and len(first) < len(HEADER):
            # Older header (fewer bot columns): extend it in place.
            ws.update(values=[HEADER], range_name="A1", value_input_option="RAW")
            log.info("Added columns %s to %r", HEADER[len(first):], ws.title)
        elif first[: len(HEADER)] != HEADER:
            raise RuntimeError(
                f"Worksheet {ws.title!r} has an unexpected header row. "
                f"Expected: {HEADER}. Rename that tab or change SHEET_WORKSHEET.")

    def _header_notes(self, ws: gspread.Worksheet) -> None:
        try:
            ws.update_notes({rowcol_to_a1(1, n): self._note(col) for n, col in enumerate(HEADER, start=1)})
            ws.freeze(rows=1)
        except Exception:  # notes are a convenience only
            log.exception("Couldn't add header notes to %r", ws.title)

    # ---------- reads ----------

    def _records(self) -> list[dict]:
        """This month's rows, each with its sheet row number in "_row"."""
        ws = self.current()
        return [{**r, "_row": i + 2}  # +1 header row, +1 because rows are 1-based
                for i, r in enumerate(ws.get_all_records(expected_headers=HEADER))]

    def _history(self) -> list[dict]:
        """Rows from ALL month tabs ("<prefix> YYYY-MM"), oldest first, each
        with "_tab". Used where a decision depends on earlier months too."""
        pattern = re.compile(rf"^{re.escape(self.prefix)} \d{{4}}-\d{{2}}$")
        rows: list[dict] = []
        for ws in sorted((w for w in self.book.worksheets() if pattern.match(w.title)), key=lambda w: w.title):
            rows += [{**r, "_tab": ws.title} for r in ws.get_all_records(expected_headers=HEADER)]
        return rows

    def _month_tabs(self) -> list[gspread.Worksheet]:
        pattern = re.compile(rf"^{re.escape(self.prefix)} \d{{4}}-\d{{2}}$")
        return sorted((w for w in self.book.worksheets() if pattern.match(w.title)), key=lambda w: w.title)

    def find_rows(self, intake_ids: set[str]) -> list[dict]:
        """Rows (any month tab) whose intake_id is in `intake_ids`, each with
        "_ws" (the worksheet) and "_row" (its sheet row number)."""
        found = []
        for ws in self._month_tabs():
            for i, r in enumerate(ws.get_all_records(expected_headers=HEADER)):
                if str(r.get("intake_id", "")) in intake_ids:
                    found.append({**r, "_ws": ws, "_row": i + 2})
        return found

    def find_merge_target(self, intake_id: str, qty: int) -> dict | None:
        """The row an "Add qty" item was added to (its notes say "+qty (intake_id)")."""
        mark = f"+{qty} ({intake_id})"
        for ws in self._month_tabs():
            for i, r in enumerate(ws.get_all_records(expected_headers=HEADER)):
                if mark in str(r.get("notes", "")):
                    return {**r, "_ws": ws, "_row": i + 2}
        return None

    @staticmethod
    def delete_rows(rows: list[dict]) -> None:
        """Delete located rows, bottom-up per tab so row numbers stay valid."""
        for r in sorted(rows, key=lambda x: (x["_ws"].title, x["_row"]), reverse=True):
            r["_ws"].delete_rows(r["_row"])

    @staticmethod
    def rewrite_row(located: dict, row: dict[str, object]) -> None:
        located["_ws"].update(values=[[row.get(c, "") for c in HEADER]], range_name=f"A{located['_row']}",
                              value_input_option="RAW")

    @staticmethod
    def take_quantity(located: dict, qty: int, intake_id: str) -> int:
        """Undo an "Add qty": subtract it and drop its note. Returns the new quantity."""
        ws = located["_ws"]
        new_qty = max(0, int(located.get("quantity") or 0) - qty)
        notes = " ".join(str(located.get("notes", "")).replace(f"+{qty} ({intake_id})", " ").split())
        ws.update(values=[[new_qty]], range_name=rowcol_to_a1(located["_row"], QTY_COL), value_input_option="RAW")
        ws.update(values=[[notes]], range_name=rowcol_to_a1(located["_row"], NOTES_COL), value_input_option="RAW")
        return new_qty

    def same_sku_today(self, sku: str) -> list[dict]:
        """Rows received TODAY with this SKU. Earlier dates are separate receipts
        and aren't checked."""
        if not sku:
            return []
        want, today = sku.strip().upper(), self.today().isoformat()
        return [r for r in self._records()
                if str(r.get("preview_sku", "")).strip().upper() == want
                and str(r.get("receipt_date", "")) == today]

    def similar(self, row: dict[str, object]) -> list[dict]:
        """Rows in ANY month tab that would print the same name: same brand,
        SKU / ref code, strap, strap color, case (+ descriptor). In practice
        AC/EXP/CA models sharing a ref code. All months, not just this one:
        accounting treats a repeat SKU as a restock, so a model whose
        look-alike was received in an earlier month must still get its
        descriptor, or its stock lands on the other model."""
        keys = ("type", "brand", "ref_code", "manufacturer_sku", "strap_type", "strap_color", "case_color",
                "dial_color", "index_type", "crown_color")
        return [r for r in self._history()
                if all(str(r.get(k, "")).lower() == str(row.get(k, "")).lower() for k in keys)]

    # ---------- writes ----------

    def append(self, row: dict[str, object]) -> None:
        """A new receipt line in this month's tab."""
        self.current().append_row([row.get(c, "") for c in HEADER], value_input_option="RAW")

    def replace(self, row_number: int, row: dict[str, object]) -> None:
        """Overwrite a row (same SKU logged twice on the same day)."""
        self.current().update(values=[[row.get(c, "") for c in HEADER]], range_name=f"A{row_number}",
                              value_input_option="RAW")

    def add_quantity(self, existing: dict, add: int, note: str) -> int:
        """Add `add` units to an existing row (another unit received the same day).
        Returns the new quantity."""
        ws = self.current()
        new_qty = int(existing.get("quantity") or 0) + add
        notes = f"{existing.get('notes', '')} {note}".strip()
        ws.update(values=[[new_qty]], range_name=rowcol_to_a1(existing["_row"], QTY_COL), value_input_option="RAW")
        ws.update(values=[[notes]], range_name=rowcol_to_a1(existing["_row"], NOTES_COL), value_input_option="RAW")
        return new_qty

    # ---------- guide ----------

    def write_guide(self, samples: list[tuple[str, dict]]) -> None:
        """A Legend tab explaining every column, with worked sample rows.
        Rewritten at every start; never touches the month tabs."""
        title = "Legend"
        try:
            guide = self.book.worksheet(title)
            guide.clear()
        except gspread.WorksheetNotFound:
            guide = self.book.add_worksheet(title=title, rows=100, cols=len(HEADER) + 1)
        rows: list[list] = [
            [f"One tab per month ({self.prefix} YYYY-MM). Each row is one goods receipt with its receipt_date. "
             "Written by the bot; edits on this Legend tab are overwritten."],
            ["Download the .xlsx with the month tab selected: the importer reads only the active tab, "
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
        sample_header = len(HEADER) + 7
        guide.format(f"A{sample_header}:{rowcol_to_a1(sample_header, len(HEADER) + 1)}", {"textFormat": {"bold": True}})

    @staticmethod
    def _note(col: str) -> str:
        filled_by, required, meaning, imported = LEGEND[col]
        req = f"Required: {required}. " if required else ""
        kind = "" if imported else "Bot info (not imported). "
        return f"{kind}{req}Filled by: {filled_by}.\n{meaning}"
