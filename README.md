# CoinDCX Signal Bot v2.0 — Render Free Optimized

**Signal-only** crypto bot. Generates LONG/SHORT signals on **5-minute** timeframe. Never places orders.

## Features
- Normal Engine (trend / pullback / breakout / reversal) + Momentum Engine (explosion / squeeze / continuation / qualified volume spike)
- Dual-session crypto ORB: London 08:00 Europe/London and New York 09:30 America/New_York with 15-minute opening ranges and DST-aware clocks
- 10-point scoring, ATR risk (SL / TP1 / TP2 / TP3)
- Telegram push alerts to configured admin IDs (push-only; command polling is disabled)
- FastAPI health: `/health` `/ping` `/status` `/metrics`
- CoinDCX REST (markets_details + candles with 5m support)
- Designed for Render Free Tier + external cron keep-alive

## Quick Deploy (Render Free)

1. Push this repo to GitHub
2. Render → New Web Service → connect repo
3. **Build:** `pip install -r requirements.txt`
4. **Start:** `uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1`
5. Instance: **Free** · Health Check Path: `/ping`
6. Environment variables:
   - `TELEGRAM_BOT_TOKEN` = from @BotFather
   - `TELEGRAM_ADMIN_IDS` = your numeric Telegram user ID (from @userinfobot)
   - `APP_ENV=production`
   - `SIGNAL_THRESHOLD=7.0`
   - `DATABASE_URL` = the Internal Database URL from a managed Render PostgreSQL instance (keep it secret)
7. Keep exactly **one Render service instance** and the configured **one Uvicorn worker**. Do not deploy a second copy with the same Telegram bot token.
8. Deploy
9. Verify `/health` and `/status`, then wait for completed-candle alerts to be pushed to the configured admin IDs. Telegram command polling is not enabled.
10. Cron-job.org → GET `https://YOUR-SERVICE.onrender.com/ping` every 10–14 min

## Keep-alive and health checks

- `GET /ping` and `HEAD /ping` return HTTP `204 No Content`: no response body, JSON, database query, exchange request, Telegram call, or scan work.
- Render uses `/ping` as its process-liveness check so a temporary PostgreSQL outage does not mark the web process dead.
- Use `/health` for database/engine readiness diagnostics; it can return `503` while scanning is deliberately paused.
- An external cron should request `/ping` every 10–14 minutes. This is best-effort on Render Free and does not override Render suspension policies or guarantee uninterrupted operation.

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
- Evaluate once per completed 5-minute candle after the configured close grace; the separate pending-retest monitor checks fresh LTP every 2 seconds

## Important
- Free tier spins down after ~15 min idle — use external `/ping` cron
- Keep-alive is best-effort and uses free instance hours
- Signal-only — no order execution

## License
MIT (bot code). CoinDCX API subject to their terms.


## Signal-quality and safety behavior (audit upgrade)

- REST candle ingestion normalizes timestamps to UTC epoch milliseconds, removes malformed/duplicate rows, and passes completed candles only.
- REST is authoritative for completed-candle decisions and uses a short shared cache. WebSocket is optional in local configuration; the Render blueprint explicitly enables it for live Futures LTP and forming-candle watchlist telemetry.
- Normal alerts require directional 15m EMA9/21 context, 5m EMA/VWAP/ADX/RSI alignment, a quality candle close, anti-chase distance, and a recognized strategy predicate. Directional +DI/-DI alignment and rising ADX improve the ADX score but are not hard gates, preserving earlier entries before confirmation fully develops.
- Momentum alerts require a qualified explosion, squeeze release, continuation, directional volume spike, or ORB state with directional confirmation; generic setup labels cannot trigger alerts.
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


## Dual-session crypto ORB

- ORB evaluates London and New York independently on completed 5-minute CoinDCX Futures candles.
- London opens at 08:00 Europe/London; New York opens at 09:30 America/New_York. IANA time zones automatically handle UK/US daylight-saving changes.
- Each opening range is the first three 5-minute candles (15 minutes). Breakout evaluation begins only after the range is complete.
- Fresh breakout candidates are accepted for up to 180 minutes after the opening range closes, subject to RVOL >= 1.30, directional candle/body quality, and maximum 0.80 ATR extension.
- Missing opening-range candles invalidate that session's ORB rather than constructing a partial range.
- If both sessions qualify on one scan, the newer session's candidate takes precedence; a valid London candidate remains eligible while New York is still building its range.
- Telegram ORB alerts identify the session and local opening time.
- Both sessions are enabled by default. Set ORB_LONDON_ENABLED or ORB_NEW_YORK_ENABLED to false to disable one.
- Application safety is enforced in config.py: settings.signal_only is always true and settings.orders_enabled is always false. No order-placement code is introduced.


### Static universe and liquidity protection

The Normal and Momentum/ORB YAML files remain static candidate allowlists (40 and 80 entries respectively). At startup and every 15 minutes, active CoinDCX USDT Futures candidates must also pass a minimum estimated 24-hour quote-turnover floor:

- Normal: `NORMAL_MIN_24H_TURNOVER_USDT=1000000`
- Momentum and ORB: `MOMENTUM_MIN_24H_TURNOVER_USDT=500000`

CoinDCX's Futures current-prices feed documents `v` as 24-hour base volume and `ls` as last price. The bot estimates USDT turnover as `v × ls`; this is a liquidity proxy, not exact trade-by-trade quote notional. Missing turnover data fails closed. The static YAML order is preserved; candidates are excluded rather than dynamically re-ranked or replaced.

### Telegram delivery reliability

Confirmed signals are written to a database-backed outbox, one row per configured admin. A background worker retries transient delivery failures with bounded exponential backoff while the original signal entry-validity window remains open. Expired alerts are not sent late. Delivery is at-least-once: a rare process crash after Telegram accepts a message but before the database records success can cause a duplicate on retry. PostgreSQL is required for durable Render production operation; local SQLite is for development only.
