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

1. Send `/start` (or any message). The bot says hi and asks what to do:
   **📦 1. Intake** or **🧰 2. Other tasks** (tap, or type 1 / 2).
2. **Intake** opens a receiving session. Send one photo per item as below.
   A photo sent with no session open asks "Start intake & use it?".
3. Tap **⏹ End intake** (kept at the bottom of the chat) or send `/end`.
   If items are still unconfirmed, the bot lists them first: show them,
   discard them and end, or keep receiving.
4. You get an **intake summary**: items, pieces, new vs restock, value at
   cost (HBB) and list (HJB), cancelled, and which sheet tab to import.

**Check a SKU or model (any time):** `cek SRPD53K1` or `cek AC 5014` (also
`/cek`, or via 🧰 Other tasks). Shows whether it's in accounting with its
stock (every variant for an AC/EXP/CA model) and what the bot has
received for it. Read-only.

**Undo a save:** `revoke <id>` (or `batal <id>`, `/revoke <id>`) with the id
from the confirmation: a group id (`20261125-8a6b15`) revokes the whole
group, `20261125-8a6b15-3` one watch of it, a single item id that item.
The bot lists what will change and asks first. A new line is removed; an
"Add qty" is taken off the line it went into; a "Replace" puts the
replaced line back. Rows from an earlier day may already be in
accounting: the bot warns, and those must be fixed there too.

### Each item

Send one photo, or an album with the item, tag and box, with a caption like:

```
Seiko 1.250.000 sku SRPD53K1 G ss silver bd silver 1pcs
AC 950k ref 1007 L leather black bd rose gold qty 2
Bonia 400rb Ladies sku BNB10123
Seiko JD 250k sku QXA756W        (JD or "clock" = a clock)
```

- **Several watches in one photo** (e.g. all colours of one model): one
  caption for all, with the **total pieces as a checker**:
  `Ac 5014 / semua warna 2.000.000 / 5 pcs`. Each watch becomes its own
  line (1 pc each). The bot lists them 1, 2, 3 … left to right; fix one by
  number (`3 dial black`, `2 qty 3`, `hapus 5`) or all (`semua L`). It won't
  save until the pieces match the caption total, or staff confirm the count.
  Gender is proposed from the model's existing products, else asked once.
  For AC/EXP/CA the model number after the brand is the ref (`Ac 5014`).
- **Different price / gender per colour** in one photo: one caption line
  per colour, starting with the colour:
  ```
  AC 1044
  Silver 980.000 L
  Full black 1.180.000 G
  Rose Gold 1.180.000 L
  ```
  A colour line applies to watches with that case colour; `Full <colour>`
  to bracelet and case both that colour (wins over a plain colour line);
  `Two tone` / `Kombi` to two-tone bracelets. Several colours on one line:
  `Gold / Rose Gold 1.900.000 L` (small typos like `Rose Golf` are corrected
  and shown). A number that isn't a valid price is flagged, never guessed.
  Fix several at once: `1,3 1.900.000`. Other lines apply to all. A
  watch no line matches is flagged and blocks Confirm until fixed, e.g.
  `4 1.180.000 G`.
- **Strap and colors** can be typed in tag order, `{strap} {strap color} bd
  {case color}` (names or codes: `ss silver bd silver`, `LS BLK BD RSG`).
  Typed values override what Claude reads from the photo, so a full
  caption works even if the photo can't be read. Quantity: `1pcs`,
  `2 pcs`, `qty 2`, `x2`.
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

Open the sheet → this month's tab (e.g. `Intake 2026-11`) → File → Download → Microsoft Excel →
upload at `/products-import`. The importer only reads the **active** tab,
so have the month tab (e.g. `Intake 2026-11`) selected when you download. The audit columns after the
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
