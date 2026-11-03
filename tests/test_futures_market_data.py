import pytest

from exchange.coindcx_rest import CoinDCXRest


@pytest.mark.asyncio
async def test_futures_15m_candles_are_aggregated_from_three_completed_5m_bars(monkeypatch):
    base = 1_800_000_000
    monkeypatch.setattr("exchange.coindcx_rest.time.time", lambda: base + 1200)
    client = CoinDCXRest()

    async def fake_get(url, params=None):
        assert url.endswith("/market_data/candlesticks")
        assert params["resolution"] == "5"
        assert params["pcode"] == "f"
        return [
            {"time": base, "open": 100, "high": 102, "low": 99, "close": 101, "volume": 10},
            {"time": base + 300, "open": 101, "high": 103, "low": 100, "close": 102, "volume": 20},
            {"time": base + 600, "open": 102, "high": 104, "low": 101, "close": 103, "volume": 30},
        ]

    client._get = fake_get
    candles = await client.get_candles("B-BTC_USDT", "15m", limit=10)

    assert len(candles) == 1
    assert candles[0] == {
        "time": base * 1000, "open": 100.0, "high": 104.0,
        "low": 99.0, "close": 103.0, "volume": 60.0,
    }


def test_futures_ws_price_parser_uses_last_trade_not_mark_price(monkeypatch):
    client = CoinDCXRest()
    now_ms = 1_800_000_000_000
    monkeypatch.setattr("exchange.coindcx_rest.time.time", lambda: now_ms / 1000)

    client.set_ws_prices({
        "B-BTC_USDT": {"ls": "65000.5", "mp": "65100.0", "ts": now_ms},
        "B-ETH_USDT": {"mp": "3000.0", "ts": now_ms},
    }, now_ms)

    assert client._ws_prices["BTCUSDT"][0] == 65000.5
    assert "ETHUSDT" not in client._ws_prices


@pytest.mark.asyncio
async def test_futures_price_endpoint_normalizes_nested_payload_and_ltp(monkeypatch):
    client = CoinDCXRest()
    now_ms = 1_800_000_000_000
    monkeypatch.setattr("exchange.coindcx_rest.time.time", lambda: now_ms / 1000)

    async def fake_get(url, params=None):
        assert url.endswith("/market_data/v3/current_prices/futures/rt")
        return {"prices": {"B-BTC_USDT": {"ls": "65000", "mp": "65100"}}}

    client._get = fake_get
    rows = await client.get_ticker()
    assert rows[0]["market"] == "B-BTC_USDT"
    assert rows[0]["last_price"] == "65000"
    assert await client.get_last_price("B-BTC_USDT") == 65000.0


@pytest.mark.asyncio
async def test_active_futures_instruments_string_list_is_normalized(monkeypatch):
    client = CoinDCXRest()

    async def fake_get(url, params=None):
        assert url.endswith("/exchange/v1/derivatives/futures/data/active_instruments")
        assert params == {"margin_currency_short_name[]": "USDT"}
        return ["B-BTC_USDT", "B-ETH_USDT", "B-BTC_USDT", "B-USDC_USDT", "B-INVALID_INR"]

    client._get = fake_get
    markets = await client.get_market_details()

    assert [row["pair"] for row in markets] == ["B-BTC_USDT", "B-ETH_USDT", "B-USDC_USDT"]
    assert markets[0]["target_currency_short_name"] == "BTC"
    assert markets[0]["base_currency_short_name"] == "USDT"
    assert all(row["status"] == "active" for row in markets)


@pytest.mark.asyncio
async def test_active_futures_instruments_accept_wrapped_and_metadata_shapes():
    client = CoinDCXRest()
    payload = {
        "data": [
            {"pair": "B-SOL_USDT", "status": "active", "margin_currency_short_name": "USDT",
             "target_currency_short_name": "SOL"},
            {"pair": "B-XRP_USDT", "status": "inactive", "margin_currency_short_name": "USDT"},
            {"pair": "B-ADA_INR", "status": "active", "margin_currency_short_name": "INR"},
        ]
    }

    async def fake_get(url, params=None):
        return payload

    client._get = fake_get
    markets = await client.get_market_details()
    assert [row["pair"] for row in markets] == ["B-SOL_USDT"]
