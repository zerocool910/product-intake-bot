"""Settings from environment / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    allowed_user_ids: frozenset[int]
    claude_model: str
    google_credentials_file: str
    spreadsheet_id: str
    worksheet: str
    timezone: str
    data_dir: Path
    accounting_api_url: str = ""
    accounting_api_token: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(ROOT / ".env")

        def need(key: str) -> str:
            value = os.environ.get(key, "").strip()
            if not value:
                raise SystemExit(f"Missing required setting {key} (see .env.example)")
            return value

        allowed = frozenset(int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").replace(" ", "").split(",") if x)
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("Missing required setting ANTHROPIC_API_KEY (see .env.example)")

        return cls(
            telegram_token=need("TELEGRAM_BOT_TOKEN"),
            allowed_user_ids=allowed,
            claude_model=os.environ.get("CLAUDE_MODEL", "claude-opus-5-5"),
            google_credentials_file=need("GOOGLE_SERVICE_ACCOUNT_FILE"),
            spreadsheet_id=need("SHEET_ID"),
            worksheet=os.environ.get("SHEET_WORKSHEET", "Intake"),
            timezone=os.environ.get("TIMEZONE", "Asia/Jakarta"),
            data_dir=Path(os.environ.get("DATA_DIR", ROOT / "data")),
            accounting_api_url=os.environ.get("ACCOUNTING_API_URL", "").strip(),
            accounting_api_token=os.environ.get("ACCOUNTING_API_TOKEN", "").strip(),
        )
