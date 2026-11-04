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
"""


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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    @staticmethod
    def new_id() -> str:
        # Short, sortable-enough, and safe in file names.
        return datetime.now().strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:6]

    def create(self, intake_id: str, chat_id: int, user_id: int, user_name: str,
               fields: ItemFields, photos: list[str]) -> Intake:
        now = _now()
        self.db.execute(
            "INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?,?)",
            (intake_id, chat_id, user_id, user_name, "pending", fields.model_dump_json(),
             json.dumps(photos), None, now, now))
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

    @staticmethod
    def _row(row: sqlite3.Row) -> Intake:
        return Intake(id=row["id"], chat_id=row["chat_id"], user_id=row["user_id"],
                      user_name=row["user_name"], status=row["status"],
                      fields=ItemFields.model_validate_json(row["fields"]),
                      photos=json.loads(row["photos"]), message_id=row["message_id"],
                      created_at=row["created_at"])
