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
    assert [row["pair"] for row in markets] == ["B-SOL_USDT", "B-XRP_USDT"]
    assert markets[1]["status"] == "inactive"


@pytest.mark.asyncio
async def test_futures_price_endpoint_normalizes_list_payload_and_volume(monkeypatch):
    client = CoinDCXRest()
    now_ms = 1_800_000_000_000
    monkeypatch.setattr("exchange.coindcx_rest.time.time", lambda: now_ms / 1000)

    async def fake_get(url, params=None):
        return [
            {"market": "B-BTC_USDT", "ls": "65000", "mp": "65100", "ts": now_ms, "v": "12.5"},
            {"pair": "B-ETH_USDT", "lastPrice": "3000", "timestamp": now_ms, "volume": "20"},
        ]

    client._get = fake_get
    rows = await client.get_ticker()
    assert len(rows) == 2
    assert rows[0]["last_price"] == "65000"
    assert rows[0]["base_volume"] == "12.5"
    assert await client.get_last_price("B-BTC_USDT") == 65000.0
    assert await client.get_last_price("B-ETH_USDT") == 3000.0


@pytest.mark.asyncio
async def test_futures_price_endpoint_uses_root_timestamp_and_v_volume(monkeypatch):
    client = CoinDCXRest()
    now_ms = 1_800_000_000_000
    monkeypatch.setattr("exchange.coindcx_rest.time.time", lambda: now_ms / 1000)

    async def fake_get(url, params=None):
        return {"ts": now_ms, "prices": {"B-SOL_USDT": {"ls": "150", "v": "1000"}}}

    client._get = fake_get
    rows = await client.get_ticker()
    assert rows[0]["timestamp"] == now_ms
    assert rows[0]["base_volume"] == "1000"
    assert await client.get_last_price("B-SOL_USDT") == 150.0


@pytest.mark.asyncio
async def test_futures_price_missing_timestamp_fails_closed(monkeypatch):
    client = CoinDCXRest()
    now_ms = 1_800_000_000_000
    monkeypatch.setattr("exchange.coindcx_rest.time.time", lambda: now_ms / 1000)

    async def fake_get(url, params=None):
        return {"prices": {"B-BTC_USDT": {"ls": "65000"}}}

    client._get = fake_get
    assert await client.get_last_price("B-BTC_USDT") is None


def test_futures_ws_price_handler_accepts_documented_event_payload(monkeypatch):
    from exchange.coindcx_ws import CoinDCXFuturesStream

    class CandleStub:
        def on_candle(self, *args, **kwargs):
            pass

    rest = CoinDCXRest()
    now_ms = 1_800_000_000_000
    monkeypatch.setattr("exchange.coindcx_rest.time.time", lambda: now_ms / 1000)
    stream = CoinDCXFuturesStream(CandleStub(), rest)
    stream._handle_prices({"data": {
        "ts": now_ms,
        "prices": {"B-BTC_USDT": {"ls": "65000", "mp": "65100", "ts": now_ms}}
    }})
    assert rest._ws_prices["BTCUSDT"][0] == 65000.0
