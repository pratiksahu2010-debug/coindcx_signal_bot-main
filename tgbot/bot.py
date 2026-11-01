"""Telegram bot integration — signal delivery + commands."""
from typing import Optional, List, Any, Dict
from datetime import datetime, timezone
import asyncio
import re
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import TelegramError, Conflict
from sqlalchemy import text as sql_text

from config import settings
from utils.logger import logger
from utils.time_utils import utc_iso


def _esc(text: Any) -> str:
    """Escape Telegram MarkdownV2 special chars. Safe for None."""
    if text is None:
        return "n/a"
    s = str(text)
    # Escape characters that break legacy Markdown
    for ch in ("_", "*", "`", "["):
        s = s.replace(ch, "\\" + ch)
    return s


def _num(val: Any, decimals: int = 4) -> str:
    """Format number safely for Telegram."""
    if val is None:
        return "n/a"
    try:
        f = float(val)
        if abs(f) >= 1000:
            return f"{f:,.2f}"
        if abs(f) >= 1:
            return f"{f:.4f}".rstrip("0").rstrip(".")
        return f"{f:.6f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(val)


class TelegramBot:
    def __init__(self):
        self.token = settings.telegram_bot_token
        self.app: Optional[Application] = None
        self.bot: Optional[Bot] = None
        self.admin_ids: List[int] = settings.admin_ids
        self.polling_active = False
        self.polling_conflict = False
        self._polling_lock_connection = None
        self._polling_lock_id = 73190420261101

    async def start(self) -> None:
        if not self.token:
            logger.warning("TELEGRAM_BOT_TOKEN not set — Telegram disabled")
            return
        try:
            self.app = Application.builder().token(self.token).build()
            self.bot = self.app.bot
            self._register_handlers()
            await self.app.initialize()
            await self.app.start()

            # In production, Telegram polling is guarded by a PostgreSQL
            # session-level advisory lock. The lock connection stays open for
            # the entire polling lifetime, so a second Render service cannot
            # poll with the same token while sharing the same database.
            may_poll = not settings.is_production
            if settings.use_postgres:
                try:
                    from database import db as database_db
                    if database_db.engine is None:
                        raise RuntimeError("database engine is not initialized")
                    self._polling_lock_connection = await database_db.engine.connect()
                    acquired = await self._polling_lock_connection.scalar(
                        sql_text("SELECT pg_try_advisory_lock(:lock_id)"),
                        {"lock_id": self._polling_lock_id},
                    )
                    may_poll = bool(acquired)
                    await self._polling_lock_connection.commit()
                    if not may_poll:
                        await self._polling_lock_connection.close()
                        self._polling_lock_connection = None
                        logger.warning("Telegram polling not started: another instance owns the PostgreSQL polling lock")
                except Exception as lock_error:
                    may_poll = False
                    if self._polling_lock_connection is not None:
                        await self._polling_lock_connection.close()
                        self._polling_lock_connection = None
                    logger.error(f"Telegram polling disabled because singleton lock could not be acquired: {lock_error}")
            elif settings.is_production:
                may_poll = False
                logger.error("Telegram polling disabled in production: configure shared PostgreSQL DATABASE_URL for duplicate-instance protection")

            if may_poll and self.app.updater:
                try:
                    await self.app.updater.start_polling(
                        drop_pending_updates=True,
                        error_callback=self._on_polling_error,
                    )
                    self.polling_active = True
                except Conflict as exc:
                    self.polling_conflict = True
                    logger.error(f"Telegram polling conflict at startup; polling disabled: {exc}")
                except Exception as exc:
                    logger.error(f"Telegram polling failed to start: {exc}")
            logger.info(
                f"Telegram bot initialized (admins: {self.admin_ids}, "
                f"polling: {'active' if self.polling_active else 'disabled'})"
            )
        except Exception as e:
            logger.error(f"Telegram start failed: {e}")
            self.bot = None

    def _on_polling_error(self, error: BaseException) -> None:
        """Stop retry storms if Telegram reports another getUpdates consumer."""
        if isinstance(error, Conflict):
            self.polling_conflict = True
            self.polling_active = False
            logger.critical(
                "Telegram getUpdates conflict detected. Polling is being stopped; "
                "signal delivery remains available. Ensure only one service uses this token."
            )
            try:
                asyncio.get_running_loop().create_task(self._stop_conflicted_poller())
            except RuntimeError:
                pass
        else:
            logger.warning(f"Telegram polling network error: {error}")

    async def _stop_conflicted_poller(self) -> None:
        try:
            if self.app and self.app.updater and self.app.updater.running:
                await self.app.updater.stop()
        except Exception as exc:
            logger.debug(f"Stopping conflicted Telegram poller: {exc}")

    async def stop(self) -> None:
        try:
            if self.app and getattr(self.app, "updater", None) and self.app.updater.running:
                await self.app.updater.stop()
            self.polling_active = False
            if self.app and self.app.running:
                await self.app.stop()
            if self.app:
                await self.app.shutdown()
        except Exception as e:
            logger.debug(f"Telegram stop: {e}")
        finally:
            if self._polling_lock_connection is not None:
                try:
                    await self._polling_lock_connection.execute(
                        sql_text("SELECT pg_advisory_unlock(:lock_id)"),
                        {"lock_id": self._polling_lock_id},
                    )
                    await self._polling_lock_connection.commit()
                    await self._polling_lock_connection.close()
                except Exception as exc:
                    logger.warning(f"Telegram polling lock release note: {exc}")
                self._polling_lock_connection = None

    def _register_handlers(self) -> None:
        assert self.app
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(CommandHandler("ping", self.cmd_ping))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("signals", self.cmd_signals))

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        uid = update.effective_user.id if update.effective_user else 0
        await update.message.reply_text(
            f"🚀 CoinDCX Signal Bot v2.0\n\n"
            f"Signal-only system. No automated trading.\n"
            f"Your ID: {uid}\n"
            f"Use /help for commands.\n"
            f"Use /ping for connectivity."
        )

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "Commands\n"
            "/ping — Connectivity (no scan)\n"
            "/status — System health link\n"
            "/signals — Info\n"
            "/help — This message\n\n"
            "Signals are pushed automatically when score ≥ threshold."
        )

    async def cmd_ping(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            f"🟢 BOT CONNECTIVITY\n"
            f"Web Service: Online\n"
            f"Timestamp: {utc_iso()}\n"
            f"No full scan triggered."
        )

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "Live status:\nhttps://coindcx-signal-bot.onrender.com/status"
        )

    async def cmd_signals(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("Active signals are pushed live when generated.")

    def format_signal(self, signal: Dict[str, Any]) -> str:
        """Plain-text friendly format (no Markdown parse issues)."""
        risk = signal.get("risk") or {}
        ind = signal.get("indicators") or {}
        direction = signal.get("direction", "")
        emoji = "🟢 LONG" if direction == "LONG" else "🔴 SHORT"
        grade = signal.get("grade", "")
        score = signal.get("score", 0)
        pair = signal.get("display") or signal.get("pair", "")
        strategy = signal.get("strategy", "")
        engine = (signal.get("engine") or "").upper()
        price = signal.get("price")

        lines = [
            "🚀 COINDCX SIGNAL BOT",
            "",
            f"PAIR: {pair}",
            f"SIGNAL: {emoji}",
            f"ENGINE: {engine}",
            f"STRATEGY: {strategy}",
            f"SCORE: {score}/10 — {grade}",
            "",
        ]

        if risk:
            ez = risk.get("entry_zone") or [risk.get("entry"), risk.get("entry")]
            rr = risk.get("rr") or [0, 0, 0]
            lines += [
                f"ENTRY: {_num(risk.get('entry'))}",
                f"ENTRY ZONE: {_num(ez[0])} – {_num(ez[1])}",
                f"STOP LOSS: {_num(risk.get('stop_loss'))}",
                f"TP1: {_num(risk.get('tp1'))}",
                f"TP2: {_num(risk.get('tp2'))}",
                f"TP3: {_num(risk.get('tp3'))}",
                f"RISK: {_num(risk.get('risk_pct'), 2)}%",
                f"R:R: 1:{_num(rr[0], 2)} / 1:{_num(rr[1], 2)} / 1:{_num(rr[2], 2)}",
                "",
            ]
        else:
            lines.append(f"PRICE: {_num(price)}")
            lines.append("")

        lines += [
            "TIMEFRAME: 5M | CONFIRM: 15M",
            f"EMA 9/21: {_num(ind.get('ema9'))} / {_num(ind.get('ema21'))}",
            f"RSI: {_num(ind.get('rsi'), 2)} | ADX: {_num(ind.get('adx'), 2)}",
            f"ATR%: {_num(ind.get('atr_pct'), 3)} | RVOL: {_num(ind.get('rvol'), 2)}",
            f"MACD hist: {_num(ind.get('macd_hist'))}",
            f"CANDLE OPEN UTC: {datetime.fromtimestamp(signal['candle_time_ms'] / 1000, tz=timezone.utc).isoformat() if signal.get('candle_time_ms') else 'n/a'}",
            "",
            "Signal only — no automated trading.",
            f"SIGNAL GENERATED UTC: {signal.get('timestamp', '')}",
        ]
        return "\n".join(lines)

    def _keyboard(self, signal: Dict[str, Any]) -> InlineKeyboardMarkup:
        pair = signal.get("pair", "")[:40]
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📊 Details", callback_data=f"det:{pair}"),
                InlineKeyboardButton("🎯 Targets", callback_data=f"tgt:{pair}"),
            ],
            [
                InlineKeyboardButton("✅ TP1", callback_data=f"tp1:{pair}"),
                InlineKeyboardButton("✅ TP2", callback_data=f"tp2:{pair}"),
            ],
            [
                InlineKeyboardButton("✅ TP3", callback_data=f"tp3:{pair}"),
                InlineKeyboardButton("🛑 SL Hit", callback_data=f"sl:{pair}"),
            ],
        ])

    async def send_signal(self, signal: Dict[str, Any]) -> None:
        """Broadcast signal to all admin chat IDs (plain text — no Markdown parse errors)."""
        if not self.bot:
            logger.warning("Telegram bot not ready — signal not sent")
            return
        if not self.admin_ids:
            logger.warning("TELEGRAM_ADMIN_IDS empty — no recipients")
            return
        text = self.format_signal(signal)
        markup = self._keyboard(signal)
        for chat_id in self.admin_ids:
            try:
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=None,  # plain text — avoids entity parse errors
                    reply_markup=markup,
                    disable_web_page_preview=True,
                )
                logger.info(
                    f"Telegram signal sent to {chat_id}: "
                    f"{signal.get('pair')} {signal.get('direction')}"
                )
            except TelegramError as e:
                logger.error(f"Telegram send failed to {chat_id}: {e}")
            except Exception as e:
                logger.error(f"Telegram unexpected error to {chat_id}: {e}")
