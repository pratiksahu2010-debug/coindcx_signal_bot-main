import asyncio

from exchange.symbol_manager import SymbolManager


class FakeRest:
    def __init__(self, details):
        self.details = details

    async def get_market_details(self):
        return self.details

    async def get_ticker(self):
        return [{"market": "B-BTC_USDT", "base_volume": "10000", "last_price": "1000"}]


def test_momentum_whitelist_reports_not_listed_and_inactive_symbols(monkeypatch):
    async def run():
        rest = FakeRest([
            {
                "pair": "B-EOS_USDT",
                "coindcx_name": "B-EOS_USDT",
                "target_currency_short_name": "EOS",
                "base_currency_short_name": "USDT",
                "status": "inactive",
            },
            {
                "pair": "B-BTC_USDT",
                "coindcx_name": "B-BTC_USDT",
                "target_currency_short_name": "BTC",
                "base_currency_short_name": "USDT",
                "status": "active",
            },
        ])
        manager = SymbolManager(rest)

        def load_yaml(path):
            if path.endswith("momentum_symbols.yaml"):
                return ["B-SHIB_USDT", "B-EOS_USDT", "B-BTC_USDT"]
            return []

        monkeypatch.setattr(manager, "_load_yaml", load_yaml)
        await manager.load_and_validate()

        assert manager.whitelist_diagnostics["SHIBUSDT"]["status"] == "NOT_LISTED"
        assert manager.whitelist_diagnostics["EOSUSDT"]["status"] == "INACTIVE"
        assert manager.momentum_symbols == ["B-BTC_USDT"]

    asyncio.run(run())
