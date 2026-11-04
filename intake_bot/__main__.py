"""python -m intake_bot"""

import logging

from .bot import build_application
from .config import Settings


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = Settings.from_env()
    if not settings.allowed_user_ids:
        logging.warning("ALLOWED_USER_IDS is empty — nobody can use the bot. "
                        "Message the bot once; it replies with your ID to add.")
    build_application(settings).run_polling()


if __name__ == "__main__":
    main()
