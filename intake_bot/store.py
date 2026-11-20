"""SQLite store for intake items, so a restart doesn't lose pending items."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .item import ItemFields

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id          TEXT PRIMARY KEY,
    chat_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    user_name   TEXT NOT NULL,
    status      TEXT NOT NULL,           -- pending | confirmed | cancelled
    fields      TEXT NOT NULL,           -- ItemFields JSON
    photos      TEXT NOT NULL,           -- JSON list of local paths
    message_id  INTEGER,                 -- bot's latest summary message
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS items_pending ON items (chat_id, user_id, status);
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    user_name   TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    ended_at    TEXT
);
"""

# Added after the first release; created on start if missing.
ITEM_COLUMNS = {
    "session_id": "INTEGER",   # intake session the item belongs to
    "outcome": "TEXT",         # new | restock (from the product master check), or ''
    "saved_action": "TEXT",    # added | replaced | merged: what Confirm did in the sheet
    "saved_qty": "INTEGER",
    "saved_name": "TEXT",
    "saved_sku": "TEXT",
    "saved_hbb": "REAL",
    "saved_hjb": "REAL",
}


@dataclass
class Intake:
    id: str
    chat_id: int
    user_id: int
    user_name: str
    status: str
    fields: ItemFields
    photos: list[str]
    message_id: int | None
    created_at: str
    session_id: int | None = None
    outcome: str | None = None
    saved_action: str | None = None
    saved_qty: int | None = None
    saved_name: str | None = None
    saved_sku: str | None = None
    saved_hbb: float | None = None
    saved_hjb: float | None = None


@dataclass
class Session:
    id: int
    chat_id: int
    user_id: int
    user_name: str
    started_at: str
    ended_at: str | None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(items)")}
        for col, kind in ITEM_COLUMNS.items():
            if col not in have:
                self.db.execute(f"ALTER TABLE items ADD COLUMN {col} {kind}")
        self.db.commit()

    @staticmethod
    def new_id() -> str:
        # Short, sortable-enough, and safe in file names.
        return datetime.now().strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:6]

    def create(self, intake_id: str, chat_id: int, user_id: int, user_name: str,
               fields: ItemFields, photos: list[str], session_id: int | None = None) -> Intake:
        now = _now()
        self.db.execute(
            "INSERT INTO items (id, chat_id, user_id, user_name, status, fields, photos, message_id, "
            "created_at, updated_at, session_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (intake_id, chat_id, user_id, user_name, "pending", fields.model_dump_json(),
             json.dumps(photos), None, now, now, session_id))
        self.db.commit()
        return self.get(intake_id)

    def get(self, intake_id: str) -> Intake | None:
        row = self.db.execute("SELECT * FROM items WHERE id = ?", (intake_id,)).fetchone()
        return self._row(row) if row else None

    def by_message(self, chat_id: int, message_id: int) -> Intake | None:
        row = self.db.execute("SELECT * FROM items WHERE chat_id = ? AND message_id = ?",
                              (chat_id, message_id)).fetchone()
        return self._row(row) if row else None

    def latest_pending(self, chat_id: int, user_id: int) -> Intake | None:
        row = self.db.execute(
            "SELECT * FROM items WHERE chat_id = ? AND user_id = ? AND status = 'pending' "
            "ORDER BY created_at DESC LIMIT 1", (chat_id, user_id)).fetchone()
        return self._row(row) if row else None

    def update(self, intake_id: str, *, fields: ItemFields | None = None, status: str | None = None,
               message_id: int | None = None) -> None:
        sets, args = ["updated_at = ?"], [_now()]
        if fields is not None:
            sets.append("fields = ?"); args.append(fields.model_dump_json())
        if status is not None:
            sets.append("status = ?"); args.append(status)
        if message_id is not None:
            sets.append("message_id = ?"); args.append(message_id)
        args.append(intake_id)
        self.db.execute(f"UPDATE items SET {', '.join(sets)} WHERE id = ?", args)
        self.db.commit()

    def set_outcome(self, intake_id: str, outcome: str) -> None:
        self.db.execute("UPDATE items SET outcome = ? WHERE id = ?", (outcome, intake_id))
        self.db.commit()

    def record_saved(self, intake_id: str, *, status: str, action: str, qty: int, name: str, sku: str,
                     hbb: float | None, hjb: float | None) -> None:
        """What Confirm wrote to the sheet, for the end-of-session summary."""
        self.db.execute(
            "UPDATE items SET status = ?, saved_action = ?, saved_qty = ?, saved_name = ?, saved_sku = ?, "
            "saved_hbb = ?, saved_hjb = ?, updated_at = ? WHERE id = ?",
            (status, action, qty, name, sku, hbb, hjb, _now(), intake_id))
        self.db.commit()

    # ---------- intake sessions ----------

    def start_session(self, chat_id: int, user_id: int, user_name: str) -> Session:
        current = self.active_session(chat_id, user_id)
        if current:
            return current
        cur = self.db.execute("INSERT INTO sessions (chat_id, user_id, user_name, started_at) VALUES (?,?,?,?)",
                              (chat_id, user_id, user_name, _now()))
        self.db.commit()
        return self.session(cur.lastrowid)

    def session(self, session_id: int) -> Session | None:
        row = self.db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return Session(**dict(row)) if row else None

    def active_session(self, chat_id: int, user_id: int) -> Session | None:
        row = self.db.execute("SELECT * FROM sessions WHERE chat_id = ? AND user_id = ? AND ended_at IS NULL "
                              "ORDER BY id DESC LIMIT 1", (chat_id, user_id)).fetchone()
        return Session(**dict(row)) if row else None

    def end_session(self, session_id: int) -> Session:
        self.db.execute("UPDATE sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL", (_now(), session_id))
        self.db.commit()
        return self.session(session_id)

    def session_items(self, session_id: int) -> list[Intake]:
        rows = self.db.execute("SELECT * FROM items WHERE session_id = ? ORDER BY created_at", (session_id,))
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(row: sqlite3.Row) -> Intake:
        extra = {k: row[k] for k in ITEM_COLUMNS if k in row.keys()}
        return Intake(id=row["id"], chat_id=row["chat_id"], user_id=row["user_id"],
                      user_name=row["user_name"], status=row["status"],
                      fields=ItemFields.model_validate_json(row["fields"]),
                      photos=json.loads(row["photos"]), message_id=row["message_id"],
                      created_at=row["created_at"], **extra)
