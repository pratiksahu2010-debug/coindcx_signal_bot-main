"""Telegram bot integration — signal delivery + commands."""
from typing import Optional, List, Any, Dict
from datetime import datetime, timezone
import re
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import TelegramError

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

    async def start(self) -> None:
        if not self.token:
            logger.warning("TELEGRAM_BOT_TOKEN not set — Telegram disabled")
            return
        try:
            self.app = Application.builder().token(self.token).build()
            self.bot = self.app.bot
            await self.app.initialize()
            await self.app.start()

            # Push-only Telegram mode: this service sends alerts but never calls
            # getUpdates. This prevents polling conflicts when the same token is
            # also used by another bot process or Telegram client.
            self.polling_active = False
            logger.info(
                f"Telegram bot initialized (admins: {self.admin_ids}, "
                "mode: push-only, polling: disabled)"
            )
        except Exception as e:
            logger.exception(f"Telegram start failed: {e}")
            await self.stop()
            self.bot = None

    async def stop(self) -> None:
        # No Telegram updater is started in push-only mode.
        self.polling_active = False
        if self.app and self.app.running:
            try:
                await self.app.stop()
            except Exception as exc:
                logger.warning(f"Telegram application stop note: {exc}")
        if self.app:
            try:
                await self.app.shutdown()
            except Exception as exc:
                logger.warning(f"Telegram application shutdown note: {exc}")
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

        if engine == "SIGNALS":
            step = signal.get("step") or {}
            context = signal.get("context_15m") or {}
            quality = signal.get("quality") or {}
            ind = signal.get("indicators") or {}
            risk = signal.get("risk") or {}
            side = "🟢 CONFIRMED LONG SIGNAL" if direction == "LONG" else "🔴 CONFIRMED SHORT SIGNAL"
            def stamp(value):
                try:
                    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).isoformat()
                except (TypeError, ValueError, OverflowError):
                    return "n/a"
            rows = [
                side, "", f"Symbol: {pair}", "Exchange: CoinDCX", "Strategy: Sequential EMA 9/21 + Step Channel",
                "Timeframe: 5m", f"Candle UTC: {stamp(signal.get('candle_time_ms'))}",
                f"Candle IST: {stamp(signal.get('candle_time_ms'))}",
                "", "ENTRY CONFIRMATIONS:",
                f"EMA 9: {_num(ind.get('ema9'))}", f"EMA 21: {_num(ind.get('ema21'))}",
                f"EMA crossover UTC: {stamp(signal.get('cross_time_ms'))}",
                f"Crossover age: {signal.get('cross_age_bars', 'n/a')} completed candle(s)",
                f"VWAP: {_num(ind.get('vwap'))}", f"RSI(14): {_num(ind.get('rsi'), 2)}",
                f"ADX(14): {_num(ind.get('adx'), 2)}", f"+DI: {_num(ind.get('plus_di'), 2)}",
                f"-DI: {_num(ind.get('minus_di'), 2)}",
                "", "STEP CHANNEL:",
                f"Direction: {step.get('direction', 'UNKNOWN')}", f"Step Event: {step.get('event', 'n/a')}",
                f"MID: {_num(step.get('mid'))}", f"IN+: {_num(step.get('in_plus'))}",
                f"IN-: {_num(step.get('in_minus'))}", f"OUT+: {_num(step.get('out_plus'))}",
                f"OUT-: {_num(step.get('out_minus'))}",
                "", "15M CONTEXT:",
                f"{context.get('state', 'UNAVAILABLE')} — {context.get('alignment', 'UNAVAILABLE')}",
                "15M ROLE: INFORMATIONAL ONLY",
                "", "TRADE LEVELS:", f"Entry: {_num(risk.get('entry'))}",
                f"Stop Loss: {_num(risk.get('stop_loss'))}", f"Target 1: {_num(risk.get('tp1'))}",
                f"Target 2: {_num(risk.get('tp2'))}",
                f"Risk/Reward: {_num((risk.get('rr') or [None])[0], 2)}R / {_num((risk.get('rr') or [None, None])[1], 2)}R",
                "", "QUALITY:",
                f"Candle body ratio: {_num(quality.get('body_ratio'), 2)}",
                f"Relative volume: {_num(quality.get('rvol'), 2)}",
                f"VWAP extension: {_num(quality.get('vwap_extension_atr'), 2)} ATR",
                f"Channel extension: {_num(quality.get('channel_extension_atr'), 2)} ATR",
                f"Confirmation: {', '.join(quality.get('checklist') or [])}",
                f"Signal age: {signal.get('signal_age_seconds', 'n/a')} seconds",
                "", "Signal only — no automated trading.",
            ]
            return "\n".join(rows)

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
                f"LIVE PRICE CHECK: {_num(signal.get('live_price'))}",
                f"ENTRY CHECKED UTC: {signal.get('entry_checked_at', 'n/a')}",
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
            f"CANDLE CLOSE UTC: {datetime.fromtimestamp(signal['candle_close_time_ms'] / 1000, tz=timezone.utc).isoformat() if signal.get('candle_close_time_ms') else 'n/a'}",
            f"15M CONTEXT OPEN UTC: {datetime.fromtimestamp(signal['confirm_candle_time_ms'] / 1000, tz=timezone.utc).isoformat() if signal.get('confirm_candle_time_ms') else 'n/a'}",
            f"SIGNAL AGE AT GENERATION: {signal.get('signal_age_seconds', 'n/a')} seconds",
            "",
            "Signal only — no automated trading.",
            f"SIGNAL GENERATED UTC: {signal.get('timestamp', '')}",
        ]
        return "\n".join(lines)

    async def send_signal(self, signal: Dict[str, Any]) -> bool:
        """Push a signal to configured admins; return True if at least one delivery succeeds."""
        if not self.bot:
            logger.warning("Telegram bot not ready — signal not sent")
            return False
        if not self.admin_ids:
            logger.warning("TELEGRAM_ADMIN_IDS empty — no recipients")
            return False
        text = self.format_signal(signal)
        success_count = 0
        for chat_id in self.admin_ids:
            try:
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=None,
                    disable_web_page_preview=True,
                )
                success_count += 1
                logger.info(
                    f"Telegram signal sent to {chat_id}: "
                    f"{signal.get('pair')} {signal.get('direction')}"
                )
            except TelegramError as e:
                logger.error(f"Telegram send failed to {chat_id}: {e}")
            except Exception as e:
                logger.error(f"Telegram unexpected error to {chat_id}: {e}")
        return success_count > 0
