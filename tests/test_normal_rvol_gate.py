from core.engine import _normal_relative_volume_gate


def test_normal_rvol_gate_does_not_require_directional_candle():
    indicators = {
        "rvol": 1.25,
        "directional_volume_proxy_ok": False,
    }
    assert _normal_relative_volume_gate(indicators, "LONG", 1.2) == {
        "relative_volume": True,
    }


def test_normal_low_rvol_remains_watchlist_eligible():
    indicators = {
        "rvol": 1.05,
        "directional_volume_proxy_ok": True,
    }
    assert _normal_relative_volume_gate(indicators, "SHORT", 1.2) == {
        "relative_volume": False,
    }
