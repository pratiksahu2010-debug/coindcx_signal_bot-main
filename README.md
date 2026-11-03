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
   - `DATABASE_URL` = the Internal Database URL from a managed Render PostgreSQL instance (keep it secret)
7. Keep exactly **one Render service instance** and the configured **one Uvicorn worker**. Do not deploy a second copy with the same Telegram bot token.
9. Deploy
10. Open bot in Telegram → send `/start`
11. Cron-job.org → GET `https://YOUR-SERVICE.onrender.com/ping` every 10–14 min

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


## Production database (Render)

Production requires PostgreSQL. SQLite is supported only for local development because Render instance filesystems may be ephemeral.

1. Create a managed PostgreSQL database in the same Render region as the web service.
2. Copy its **Internal Database URL** into the web service environment variable `DATABASE_URL`.
3. Keep the URL secret; do not commit it to this repository.
4. Redeploy and verify logs show `DATABASE_READY dialect=postgresql connectivity=ok schema=ok` and health state reports `database=postgres`.
5. Keep one Render instance and one Uvicorn worker initially. Confirm pending retests and signal deduplication survive a restart. CI exercises simultaneous duplicate claims and concurrent pending-retest upserts against PostgreSQL.

The Render blueprint deliberately leaves `DATABASE_URL` unsynced so it cannot silently fall back to an ephemeral SQLite file. PostgreSQL provisioning and any associated billing remain an explicit Render dashboard action.
