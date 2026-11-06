"""Pending retest lifecycle regression tests."""
import asyncio
from core.engine import SignalEngine


class FakeRest:
    def __init__(self, prices):
        self.prices = iter(prices)

    async def get_last_price(self, pair):
        return next(self.prices)


class FakeTelegram:
    def __init__(self):
        self.sent = []

    async def send_signal(self, signal):
        self.sent.append(dict(signal))
        return True


def _pending(engine, *, expires_at, direction="LONG", stop=98.0):
    signal = {
        "pair": "B-BTC_USDT", "engine": "normal", "direction": direction,
        "score": 8.0, "grade": "SIGNAL", "price": 100.0,
        "risk": {"entry": 100.0, "entry_zone": [99.0, 100.5],
                 "stop_loss": stop, "tp1": 103.0, "tp2": 105.0,
                 "tp3": 108.0},
    }
    key = "B-BTC_USDT:normal:" + direction
    engine.pending_retests[key] = {
        "signal": signal, "expires_at": expires_at, "entry": 100.0,
        "zone_low": 99.0, "zone_high": 100.5, "stop_loss": stop,
        "candle_ts": 1_800_000_000_000,
    }
    return key


def test_pending_retest_promotes_once_only_inside_original_zone(monkeypatch):
    from database import repositories

    async def run():
        engine = SignalEngine(FakeRest([100.0, 100.1]), object(), object())
        engine.telegram = FakeTelegram()
        key = _pending(engine, expires_at=9_999_999_999_999)
        claims = []

        async def claim(signal, candle_ts, ttl_hours=4):
            claims.append(candle_ts)
            return True

        async def save(signal):
            return True

        monkeypatch.setattr(repositories, "claim_signal_once", claim)
        monkeypatch.setattr(repositories, "save_signal", save)
        await engine._check_pending_retest("B-BTC_USDT", "normal")
        await engine._check_pending_retest("B-BTC_USDT", "normal")
        assert key not in engine.pending_retests
        assert len(engine.telegram.sent) == 1
        assert engine.telegram.sent[0]["entry_type"] == "RETEST_CONFIRMED"
        assert claims == [1_800_000_000_000]
        assert engine.cooldowns["normal"].is_cooling(
            "B-BTC_USDT", "normal", "LONG"
        )

    asyncio.run(run())


def test_pending_retest_is_removed_on_invalidation():
    async def run():
        engine = SignalEngine(FakeRest([97.5]), object(), object())
        key = _pending(engine, expires_at=9_999_999_999_999, stop=98.0)
        await engine._check_pending_retest("B-BTC_USDT", "normal")
        assert key not in engine.pending_retests
        assert engine.signals["normal"].count() == 0

    asyncio.run(run())


def test_pending_retest_expires_without_requesting_price():
    async def run():
        engine = SignalEngine(FakeRest([]), object(), object())
        key = _pending(engine, expires_at=0)
        await engine._check_pending_retest("B-BTC_USDT", "normal")
        assert key not in engine.pending_retests

    asyncio.run(run())


def test_pending_retest_persistence_exception_fails_closed(monkeypatch):
    from database import repositories

    async def run():
        engine = SignalEngine(FakeRest([]), object(), object())
        key = "B-BTC_USDT:normal:LONG"
        pending = {"signal": {"pair": "B-BTC_USDT", "engine": "normal", "direction": "LONG"}}

        async def fail_save(candidate_key, record):
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(repositories, "save_pending_retest", fail_save)
        assert not await engine._persist_pending_retest_candidate(key, pending)
        assert key not in engine.pending_retests

    asyncio.run(run())


def test_pending_retest_false_persistence_result_fails_closed(monkeypatch):
    from database import repositories

    async def run():
        engine = SignalEngine(FakeRest([]), object(), object())
        key = "B-ETH_USDT:momentum:SHORT"
        pending = {"signal": {"pair": "B-ETH_USDT", "engine": "momentum", "direction": "SHORT"}}

        async def reject_save(candidate_key, record):
            return False

        monkeypatch.setattr(repositories, "save_pending_retest", reject_save)
        assert not await engine._persist_pending_retest_candidate(key, pending)
        assert key not in engine.pending_retests

    asyncio.run(run())
