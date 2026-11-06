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
        return {"ts": now_ms, "prices": {"B-BTC_USDT": {"ls": "65000", "mp": "65100"}}}

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



@pytest.mark.parametrize("row_ts,expected_source", [
    (1_800_000_000_000, "row"),
    (1_800_000_000, "row"),
])
@pytest.mark.asyncio
async def test_futures_ticker_row_timestamp_is_normalized_and_used(monkeypatch, row_ts, expected_source):
    client = CoinDCXRest()
    now_ms = 1_800_000_000_000
    monkeypatch.setattr("exchange.coindcx_rest.time.time", lambda: now_ms / 1000)

    async def fake_get(url, params=None):
        return {
            "ts": now_ms - 60_000,
            "prices": {"B-BTC_USDT": {"ls": "65000", "ts": row_ts}},
        }

    client._get = fake_get
    rows = await client.get_ticker()
    assert rows[0]["timestamp_source"] == expected_source
    assert await client.get_last_price("B-BTC_USDT") == 65000.0


@pytest.mark.parametrize("root_ts", [1_800_000_000_000, 1_800_000_000])
@pytest.mark.asyncio
async def test_futures_ticker_uses_root_timestamp_when_row_timestamp_absent(monkeypatch, root_ts):
    client = CoinDCXRest()
    now_ms = 1_800_000_000_000
    monkeypatch.setattr("exchange.coindcx_rest.time.time", lambda: now_ms / 1000)

    async def fake_get(url, params=None):
        return {"ts": root_ts, "prices": {"B-ETH_USDT": {"ls": "3000"}}}

    client._get = fake_get
    rows = await client.get_ticker()
    assert rows[0]["timestamp_source"] == "root"
    assert await client.get_last_price("B-ETH_USDT") == 3000.0


@pytest.mark.asyncio
async def test_futures_ticker_rejects_stale_row_even_when_root_timestamp_is_fresh(monkeypatch):
    client = CoinDCXRest()
    now_ms = 1_800_000_000_000
    monkeypatch.setattr("exchange.coindcx_rest.time.time", lambda: now_ms / 1000)

    async def fake_get(url, params=None):
        return {
            "ts": now_ms,
            "prices": {"B-ETH_USDT": {"ls": "3000", "ts": now_ms - 30_000}},
        }

    client._get = fake_get
    rows = await client.get_ticker()
    assert await client.get_last_price("B-ETH_USDT") is None
    assert rows[0]["timestamp_source"] == "row"


@pytest.mark.asyncio
async def test_futures_ticker_snapshot_and_symbol_lookup_share_freshness_rules(monkeypatch):
    client = CoinDCXRest()
    now_ms = 1_800_000_000_000
    monkeypatch.setattr("exchange.coindcx_rest.time.time", lambda: now_ms / 1000)

    async def fake_get(url, params=None):
        return {
            "ts": now_ms,
            "prices": {
                "B-BTC_USDT": {"ls": "65000"},
                "B-ETH_USDT": {"ls": "3000", "ts": now_ms - 30_000},
            },
        }

    client._get = fake_get
    rows = await client.get_ticker()
    ages = [client._ticker_age_seconds(row["timestamp"], now_ms) for row in rows]
    assert sum(age is not None and -5 <= age <= 10 for age in ages) == 1
    assert await client.get_last_price("B-BTC_USDT") == 65000.0
    assert await client.get_last_price("B-ETH_USDT") is None


@pytest.mark.asyncio
async def test_futures_ticker_accepts_bounded_six_second_future_clock_skew(monkeypatch):
    client = CoinDCXRest()
    now_ms = 1_800_000_000_000
    monkeypatch.setattr("exchange.coindcx_rest.time.time", lambda: now_ms / 1000)

    async def fake_get(url, params=None):
        return {"ts": now_ms + 6_000, "prices": {"B-ETH_USDT": {"ls": "3000"}}}

    client._get = fake_get
    rows = await client.get_ticker()
    age = client._ticker_age_seconds(rows[0]["timestamp"], now_ms)
    assert age == -6.0
    assert client._is_fresh_ticker_age(age, client.MAX_REST_TICKER_AGE_SECONDS)
    assert await client.get_last_price("B-ETH_USDT") == 3000.0


@pytest.mark.asyncio
async def test_futures_ticker_rejects_timestamp_beyond_future_skew_limit(monkeypatch):
    client = CoinDCXRest()
    now_ms = 1_800_000_000_000
    monkeypatch.setattr("exchange.coindcx_rest.time.time", lambda: now_ms / 1000)

    async def fake_get(url, params=None):
        return {"ts": now_ms + 16_000, "prices": {"B-ETH_USDT": {"ls": "3000"}}}

    client._get = fake_get
    assert await client.get_last_price("B-ETH_USDT") is None


def test_futures_ws_accepts_bounded_future_timestamp_skew(monkeypatch):
    client = CoinDCXRest()
    now_ms = 1_800_000_000_000
    monkeypatch.setattr("exchange.coindcx_rest.time.time", lambda: now_ms / 1000)
    accepted = client.set_ws_prices(
        {"B-BTC_USDT": {"ls": "65000", "ts": now_ms + 6_000}},
        now_ms + 6_000,
    )
    assert accepted == 1


@pytest.mark.asyncio
async def test_futures_ws_resubscribes_all_channels_after_reconnect(monkeypatch):
    from exchange.coindcx_ws import CoinDCXFuturesStream

    class CandleStub:
        def on_candle(self, *args, **kwargs):
            pass

    class SocketStub:
        def __init__(self):
            self.connected = False
            self.emitted = []
            self.handlers = {}

        def event(self, handler):
            self.handlers[handler.__name__] = handler
            return handler

        def on(self, event_name):
            def register(handler):
                self.handlers[event_name] = handler
                return handler
            return register

        async def connect(self, url, transports=None):
            self.connected = True
            self.connect_args = {"url": url, "transports": transports}
            await self.handlers["connect"]()

        async def emit(self, event, payload):
            self.emitted.append((event, payload))

        async def disconnect(self):
            self.connected = False
            await self.handlers["disconnect"]()

    socket = SocketStub()
    monkeypatch.setattr("exchange.coindcx_ws.socketio.AsyncClient", lambda **kwargs: socket)
    monkeypatch.setattr("exchange.coindcx_ws.settings.enable_websocket", True)
    stream = CoinDCXFuturesStream(CandleStub(), CoinDCXRest())

    started = await stream.start(["B-BTC_USDT", "B-ETH_USDT"])

    assert started is True
    assert socket.connect_args["transports"] == ["websocket"]
    expected = {
        "currentPrices@futures@rt",
        "B-BTC_USDT_5m-futures", "B-BTC_USDT_15m-futures",
        "B-ETH_USDT_5m-futures", "B-ETH_USDT_15m-futures",
    }
    first_joins = [payload["channelName"] for event, payload in socket.emitted if event == "join"]
    assert set(first_joins) == expected
    assert len(first_joins) == len(expected)
    assert stream._subscribed_channels == expected

    # Simulate an unexpected disconnect and the automatic Socket.IO reconnect.
    await socket.handlers["disconnect"]()
    assert stream._subscribed_channels == set()
    socket.connected = True
    await socket.handlers["connect"]()

    all_joins = [payload["channelName"] for event, payload in socket.emitted if event == "join"]
    assert all_joins.count("currentPrices@futures@rt") == 2
    assert set(all_joins) == expected
    assert len(all_joins) == len(expected) * 2
    assert stream._subscribed_channels == expected


@pytest.mark.asyncio
async def test_futures_ticker_handles_nested_data_prices_envelope(monkeypatch):
    client = CoinDCXRest()
    now_ms = 1_800_000_000_000
    monkeypatch.setattr("exchange.coindcx_rest.time.time", lambda: now_ms / 1000)

    async def fake_get(url, params=None):
        return {"data": {"ts": now_ms, "prices": {
            "B-BTC_USDT": {"ls": "65000"},
        }}}

    client._get = fake_get
    rows = await client.get_ticker()
    assert rows[0]["market"] == "B-BTC_USDT"
    assert rows[0]["timestamp"] == now_ms
    assert await client.get_last_price("B-BTC_USDT") == 65000.0


@pytest.mark.asyncio
async def test_futures_candles_handle_nested_data_candles_envelope(monkeypatch):
    base = 1_800_000_000
    monkeypatch.setattr("exchange.coindcx_rest.time.time", lambda: base + 600)
    client = CoinDCXRest()

    async def fake_get(url, params=None):
        return {"data": {"candles": [
            {"time": base, "open": 100, "high": 102, "low": 99, "close": 101, "volume": 10},
        ]}}

    client._get = fake_get
    candles = await client.get_candles("B-BTC_USDT", "5m", limit=10)
    assert len(candles) == 1
    assert candles[0]["close"] == 101.0


def test_futures_ws_candle_parser_recovers_full_pair_from_channel_and_normalizes_event_time():
    from exchange.coindcx_ws import CoinDCXFuturesStream

    class CandleStub:
        def __init__(self):
            self.rows = []

        def on_candle(self, pair, interval, candle):
            self.rows.append((pair, interval, candle))

    builder = CandleStub()
    stream = CoinDCXFuturesStream(builder, CoinDCXRest())
    base = 1_800_000_000
    stream._handle_candlestick({"data": {
        "channel": "B-BTC_USDT_5m-futures",
        "open_time": base,
        "open": 100, "high": 102, "low": 99, "close": 101, "volume": 10,
        "Ets": base + 300,
    }})
    assert len(builder.rows) == 1
    pair, interval, candle = builder.rows[0]
    assert pair == "B-BTC_USDT"
    assert interval == "5m"
    assert candle["time"] == base * 1000
    assert stream._last_event_ms == (base + 300) * 1000
