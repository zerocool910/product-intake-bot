"""python -m intake_bot"""

import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from .bot import build_application
from .config import Settings

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class RedactSecrets(logging.Filter):
    """Masks secrets (e.g. the bot token, which Telegram errors include in
    URLs and messages) before a record reaches any log destination."""

    def __init__(self, secrets: list[str]):
        super().__init__()
        self.secrets = [s for s in secrets if s and len(s) >= 8]

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.secrets:
            return True
        text = record.getMessage()
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        for s in self.secrets:
            text = text.replace(s, "<redacted>")
            if record.exc_text:
                record.exc_text = record.exc_text.replace(s, "<redacted>")
        record.msg, record.args = text, None
        return True


def setup_logging(settings: Settings | None = None) -> None:
    """Always log to stderr (systemd puts that in the journal). If LOG_FILE is
    set, also write a file rotated at midnight, keeping LOG_KEEP_DAYS days."""
    level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    log_file = os.environ.get("LOG_FILE", "").strip()
    file_error = None
    if log_file:
        try:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            handlers.append(TimedRotatingFileHandler(
                log_file, when="midnight", backupCount=int(os.environ.get("LOG_KEEP_DAYS", "30")), encoding="utf-8"))
        except OSError as e:  # e.g. run by hand before the service created /var/log/...
            file_error = e
    redact = RedactSecrets([settings.telegram_token] if settings else [])
    for h in handlers:
        h.setFormatter(logging.Formatter(FORMAT))
        h.addFilter(redact)
    logging.basicConfig(level=level, handlers=handlers, force=True)
    # httpx logs every Telegram poll (with the token in the URL) at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    if file_error:
        logging.warning("Not writing LOG_FILE %s (%s); logging to the console only.", log_file, file_error)


def main() -> None:
    setup_logging()  # so a config error is logged too
    settings = Settings.from_env()
    setup_logging(settings)
    logging.info("Starting product intake bot (model %s, sheet tab %r)", settings.claude_model, settings.worksheet)
    if not settings.allowed_user_ids:
        logging.warning("ALLOWED_USER_IDS is empty — nobody can use the bot. "
                        "Message the bot once; it replies with your ID to add.")
    else:
        logging.info("%d allowed user(s)", len(settings.allowed_user_ids))
    build_application(settings).run_polling()
    logging.info("Stopped")


if __name__ == "__main__":
    main()
