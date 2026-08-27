# Whop Checker Bot

Telegram bot that runs a Whop checkout checker: random US billing identity +
`@rabbitvore.com` email + card, rotating proxies/fingerprints per run, checking
up to 50 cards with 4 workers.

## Deploy on Railway

1. Push this repo to GitHub (or use Railway's GitHub integration).
2. Create a new Railway project from the repo.
3. Add the **BOT_TOKEN** environment variable (your Telegram bot token).
   A fallback token is baked in, but setting `BOT_TOKEN` is recommended.
4. Railway will run `python telegram_bot.py` (see `railway.json`).
   The build step installs Playwright + Chromium automatically.

## Bot commands

- `/start` — greeting + buttons (incl. 📊 Database)
- `/ccs` — add cards `num|mm|yyyy|cvv`, one per line (max 50)
- `/whop <url>` — run the check (system proxies or your own)
- `/addproxy user:pass@host:port` — tested before saving
- `/proxy` — list proxy sources
- `/live` — retry insufficient cards from the last run
- `/db` — view the database (which cards actually worked on Whop)

State (cards, proxies, last run) is persisted to `db.json` and survives
restarts/crashes. `db.json` is git-ignored — system proxies come from
`whop_bot.PROXIES`.

## Notes

- Set `BOT_TOKEN` in Railway; rotate the token if it was ever shared.
