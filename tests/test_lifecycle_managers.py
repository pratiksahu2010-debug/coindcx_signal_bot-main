"""Signal and watchlist lifecycle manager regression tests."""
from datetime import datetime, timedelta, timezone

from core.signal_manager import SignalManager
from core.watchlist_manager import WatchlistManager


def test_signal_manager_reports_capacity_evictions_for_persistence():
    manager = SignalManager(max_active=1)
    low = {
        "pair": "B-LOW_USDT", "engine": "normal", "direction": "LONG", "score": 6.5,
    }
    high = {
        "pair": "B-HIGH_USDT", "engine": "normal", "direction": "LONG", "score": 9.0,
    }
    assert manager.add(low)
    assert manager.add(high)
    assert manager.get("B-LOW_USDT", "normal", "LONG")["status"] == "evicted"
    evicted = manager.drain_evicted()
    assert len(evicted) == 1
    assert evicted[0]["pair"] == "B-LOW_USDT"
    assert manager.drain_evicted() == []


def test_watchlist_add_purges_expired_entries_before_capacity_eviction():
    manager = WatchlistManager(max_entries=1)
    manager._entries["B-OLD_USDT:normal:LONG"] = {
        "pair": "B-OLD_USDT", "engine": "normal", "direction": "LONG",
        "score": 99.0, "expires_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    }
    assert manager.add({
        "pair": "B-NEW_USDT", "engine": "normal", "direction": "LONG", "score": 1.0,
    })
    assert manager.get("B-OLD_USDT", "normal", "LONG") is None
    assert manager.get("B-NEW_USDT", "normal", "LONG") is not None


def test_watchlist_invalid_or_missing_expiry_is_treated_as_expired():
    manager = WatchlistManager()
    assert manager._expired({"expires_at": "not-a-timestamp"})
    assert manager._expired({})
    assert not manager._expired({
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
    })
