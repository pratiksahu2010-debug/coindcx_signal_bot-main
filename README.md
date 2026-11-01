# CoinDCX Signal Bot v2.0 — Render Free Optimized

**Signal-only** crypto bot. Generates LONG/SHORT signals on **5-minute** timeframe. Never places orders.

## Features
- Normal Engine (trend / pullback / breakout) + Momentum Explosion Engine
- 10-point scoring, ATR risk (SL / TP1 / TP2 / TP3)
- Interactive Telegram signals to admin IDs
- FastAPI health: `/health` `/ping` `/status` `/metrics`
- CoinDCX REST (markets_details + candles with 5m support)
- Designed for Render Free Tier + external cron keep-alive

## Quick Deploy (Render Free)

1. Push this repo to GitHub
2. Render → New Web Service → connect repo
3. **Build:** `pip install -r requirements.txt`
4. **Start:** `uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1`
5. Instance: **Free** · Health Check Path: `/health`
6. Environment variables:
   - `TELEGRAM_BOT_TOKEN` = from @BotFather
   - `TELEGRAM_ADMIN_IDS` = your numeric Telegram user ID (from @userinfobot)
   - `APP_ENV=production`
   - `SIGNAL_THRESHOLD=7.0`
   - `DATABASE_URL=sqlite+aiosqlite:///./coindcx_signal_bot.db` (already set by the included Render blueprint; no PostgreSQL setup)
7. Keep exactly **one Render service instance** and the configured **one Uvicorn worker**. Do not deploy a second copy with the same Telegram bot token.
8. Deploy
8. Open bot in Telegram → send `/start`
9. Cron-job.org → GET `https://YOUR-SERVICE.onrender.com/ping` every 10–14 min

## Local run
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edit TELEGRAM_* 
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Timeframe
- Primary: **5m** (entries)
- Confirmation: **15m**
- Scan every 60s for closed candles

## Important
- Free tier spins down after ~15 min idle — use external `/ping` cron
- Keep-alive is best-effort and uses free instance hours
- Signal-only — no order execution

## License
MIT (bot code). CoinDCX API subject to their terms.


## Signal-quality and safety behavior (audit upgrade)

- REST candle ingestion normalizes timestamps to UTC epoch milliseconds, removes malformed/duplicate rows, and passes completed candles only.
- 5m and 15m data are refreshed through a short shared REST cache; WebSocket is disabled by default because this release uses the REST scanner as its authoritative feed.
- Normal alerts require directional 15m EMA/VWAP confirmation, 5m ADX/DI alignment, quality candle close, anti-chase distance, and a recognized strategy predicate.
- Momentum alerts require a real squeeze/explosion/continuation state with directional confirmation; generic momentum labels cannot trigger alerts.
- Risk levels use nearby swing structure with ATR bounds and targets expressed as risk multiples.
- Active signals are monitored against subsequent completed candle highs/lows. If stop and target are both touched in one candle, stop-loss is conservatively assumed first.
- Signal-only operation is retained; no order-placement endpoint or execution path is added.

Backtest performance is not claimed by this release: live market data and a historical labeled dataset are required to validate win rate, expectancy, and false-positive reduction.


## SQLite deployment (no PostgreSQL required)

- The included Render blueprint sets `DATABASE_URL=sqlite+aiosqlite:///./coindcx_signal_bot.db`.
  You do not need to create a PostgreSQL database or enter a database password.
- Telegram command polling is enabled in SQLite mode. The Render service is configured
  for one Uvicorn worker; keep exactly one running Render service instance using this
  Telegram bot token. A second copy can cause Telegram's `Conflict: terminated by other
  getUpdates request` error.
- SQLite stores signal claims and bot state while the instance is running. **Render Free
  filesystem storage is ephemeral**, so a restart, redeploy, or instance replacement can
  erase this database. After that, recent signal-deduplication history may be lost and
  an otherwise valid signal could be sent again.
- The bot remains signal-only. No order execution is enabled.
- PostgreSQL remains an optional future upgrade for durable shared state and advisory-lock
  protection across multiple instances; it is not required for this single-instance setup.
- Normal and Momentum engines select independent active USDT universes from live market
  details and ticker liquidity. Defaults are 40 Normal symbols and 80 Momentum symbols;
  the YAML files are tie-break preferences/fallbacks, not hard allowlists.
- Only aligned, fully closed candles are passed to strategies. The close-publication
  grace period defaults to 3 seconds and is configurable with
  `CANDLE_CLOSE_GRACE_SECONDS`.
