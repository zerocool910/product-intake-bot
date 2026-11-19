# product-intake-bot

A Telegram bot for logging new stock. Staff photograph an item and type
the brand and price. Claude reads the photo and proposes the catalog
attributes. Staff confirm or correct in plain words, and the row lands in a
Google Sheet in exactly the column shape the accounting app's
`/products-import` expects.

- What it does and why: [`docs/product-intake-bot-spec.md`](docs/product-intake-bot-spec.md)
- Output columns: [`docs/accounting-import-columns.md`](docs/accounting-import-columns.md)
- Rules it implements: [`docs/product-naming-convention.md`](docs/product-naming-convention.md),
  [`docs/brand-pricing-reference.md`](docs/brand-pricing-reference.md)

## Using it (staff)

Send one photo, or an album with the item, tag and box, with a caption like:

```
Seiko 1.250.000 G
AC 950k ref AE1200 L qty 2
Bonia 400rb Ladies sku BNB10123
Seiko JD 250k sku QXA756W        (JD or "clock" = a clock)
```

- **List brands** (AC, EXP, CA, Seiko, Alba, Casio, Guess, Aigner): type
  the distributor's **list price**.
- **Nett brands** (Bonia, Timberland, Sezen, …): type the **cost**.
- **SALE brands** (Police, Festina, …): type the **selling price**.

The bot replies with what it read. ❓ marks anything it wasn't sure of.
Reply to that message to fix anything ("rubber not leather, ladies"), then
press ✅ Confirm. ✅ only appears once nothing required is missing.

## Install on the server

Needs Python 3.11+ and an always-on Linux machine with outbound internet.
No domain or open ports are needed: the bot connects out to Telegram
(long-polling).

```bash
sudo useradd --system --create-home --home-dir /opt/product-intake-bot intakebot
sudo -u intakebot git clone https://github.com/zerocool910/product-intake-bot /opt/product-intake-bot
cd /opt/product-intake-bot
sudo -u intakebot python3 -m venv .venv
sudo -u intakebot .venv/bin/pip install -r requirements.txt
sudo -u intakebot cp .env.example .env   # then edit .env
```

What `.env` needs:

1. **Telegram:** create a bot with @BotFather and put the token in
   `TELEGRAM_BOT_TOKEN`.
2. **Claude:** an API key from console.anthropic.com goes in `ANTHROPIC_API_KEY`.
3. **Google Sheets:** in Google Cloud, create a service account, enable the
   Google Sheets API, and download its JSON key to the path in
   `GOOGLE_SERVICE_ACCOUNT_FILE`. Create a spreadsheet, **share it with the
   service account's email as Editor**, and put its ID in `SHEET_ID`. The
   bot creates the `Intake` tab and header row itself.
4. **Allowlist:** start the bot, message it, and it replies with your
   Telegram ID. Add it (and each staff member's) to `ALLOWED_USER_IDS`,
   then restart.

Run once in the foreground to check:

```bash
sudo -u intakebot .venv/bin/python -m intake_bot
```

Then install it as a service (starts on boot, restarts if it crashes):

```bash
sudo /opt/product-intake-bot/scripts/intakebotctl install
```

After that, `intakebotctl` is a normal command:

| Command | What it does |
|---|---|
| `intakebotctl status` | Running or not, since when, last few log lines |
| `intakebotctl start` / `stop` / `restart` | Start, stop, restart the bot |
| `intakebotctl logs` | Last 100 log lines (`intakebotctl logs 500` for more) |
| `intakebotctl follow` | Live log, Ctrl+C to quit |
| `intakebotctl errors` | Only warnings and errors from the last 24 h |
| `intakebotctl update` | `git pull`, install requirements, run tests, restart; won't restart if tests fail |
| `intakebotctl uninstall` | Remove the service (keeps code, `.env`, `data/`, logs) |

Logs go to the system journal and, with `LOG_FILE` set in `.env` (the
default), also to `/var/log/product-intake-bot/bot.log`, rotated at
midnight with `LOG_KEEP_DAYS` (30) days kept. The bot token is masked in
all logs. Set `LOG_LEVEL=DEBUG` in `.env` and restart for more detail.

**Back up `data/`.** It holds the item photos and the SQLite file of
pending/confirmed items.

## Importing into accounting

Open the sheet → the `Intake` tab → File → Download → Microsoft Excel →
upload at `/products-import`. The importer only reads the **active** tab,
so have `Intake` selected when you download. The audit columns after the
import columns (`intake_id` … `notes`) are for people, and the importer
ignores them.

## Changing codes, brands or rates

Edit `config/codes.yaml` / `config/brands.yaml`, then restart. Names there
must match the accounting app's Brand / Strap Type / Color / Index Type
names exactly, because that's what the importer matches on. To check after
either side changes:

```bash
python scripts/check_accounting_seeders.py /path/to/globaltimecollection-accounting
```

A brand's `code` is what prints as `{Brand}` on the tag, unless
`print_code_on_tag: false` (Seiko, Casio) or there's no code, in
which case the full name prints. This is the same rule as the accounting
app. If the owner changes a brand on the accounting `/brands` screen, make
the same change in `config/brands.yaml`, because the checker only reads
the seeders.

## Development

```bash
pip install -r requirements.txt
python -m pytest
```

Layout:

| Path | What |
|---|---|
| `intake_bot/catalog.py` | Loads `config/*.yaml` |
| `intake_bot/pricing.py` | HBB/HJB from the typed number, by brand scheme |
| `intake_bot/naming.py` | Price cipher, printed name, AC/EXP/CA SKU |
| `intake_bot/item.py` | Item fields, required-column checks, import row |
| `intake_bot/caption.py` | Caption parsing (price, brand, gender, ref, qty) |
| `intake_bot/vision.py` | Claude calls: read photo, apply correction |
| `intake_bot/store.py` | SQLite for pending items |
| `intake_bot/sheet.py` | Google Sheets append and duplicate-name check |
| `intake_bot/bot.py` | Telegram handlers |
