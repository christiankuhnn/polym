# Polymarket trade tracker bot

This bot tracks new public trades for specified Polymarket users.

## What it does
- accepts wallet addresses or public handles like `@rdba`
- resolves handles to proxy wallets through Polymarket public search
- polls the public activity endpoint for `TRADE` events
- deduplicates seen trades using a local state file
- prints new trades to the terminal
- can optionally send the same alert to a Discord webhook

## Files
- `polymarket_tracker.py` — main bot
- `users.example.json` — sample tracked users list
- `.env.example` — sample config
- `requirements.txt` — Python dependencies

## Install
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configure
Copy the examples:
```bash
cp .env.example .env
cp users.example.json users.json
```

Then edit `users.json`:
```json
{
  "users": [
    "@rdba",
    "0x56687bf447db6ffa42ffe2204a05edaa20f55839"
  ]
}
```

## Run
```bash
python polymarket_tracker.py
```

## Optional Discord alerts
Put your webhook in `.env`:
```bash
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/your-webhook
```

## How it works
On the first run, the bot bootstraps the current feed so older trades do not spam you. After that, only unseen trades will be emitted.

## Next upgrades
- post to X instead of Discord
- filter by minimum trade size
- add SQLite storage
- export CSV logs
