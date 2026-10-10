import tempfile
import unittest
from pathlib import Path

from paper_bot.engine import PaperBot, RiskConfig, RunStore, backtest, run_live
from paper_bot.market import Candle, PublicMarketClient, Quote, SymbolRules
from paper_bot.metrics import max_drawdown_pct
from paper_bot.portfolio import Portfolio, floor_to_step
from paper_bot.strategy import BUY, SELL, SmaCrossStrategy

BTC = SymbolRules("BTCUSDT", "BTC", "USDT", step_size=0.00001, min_qty=0.00001, min_notional=5.0)


def candle(i, close, low=None, high=None):
    return Candle(i * 60_000, close, high or close, low or close, close, 1.0, i * 60_000 + 59_999)


class PortfolioTests(unittest.TestCase):
    def test_floor_to_step(self):
        self.assertEqual(floor_to_step(0.000479999, 0.00001), 0.00047)
        self.assertEqual(floor_to_step(1.29, 0.1), 1.2)

    def test_buy_then_sell_charges_fees_both_ways(self):
        pf = Portfolio(rules=BTC, initial_quote=40.0, fee_rate=0.001)
        buy = pf.buy(80_000, 0)
        self.assertAlmostEqual(buy.qty, 0.0005)  # 40 / 80000, arrondi au stepSize
        self.assertAlmostEqual(pf.quote, 0.0)
        self.assertAlmostEqual(pf.base, 0.0005 * 0.999)  # frais prélevés en BTC
        sell = pf.sell(80_000, 1)
        # 0.0004995 BTC arrondi -> 0.00049 vendable, le reste est de la poussière
        self.assertAlmostEqual(sell.qty, 0.00049)
        self.assertAlmostEqual(sell.fee_usdt, 0.00049 * 80_000 * 0.001)
        self.assertFalse(pf.in_position)
        self.assertAlmostEqual(pf.fees_paid, 0.04 + 0.0392)
        # Prix inchangé : la perte nette = frais uniquement
        self.assertAlmostEqual(pf.equity(80_000), 40 - pf.fees_paid, places=9)

    def test_min_notional_blocks_tiny_orders(self):
        pf = Portfolio(rules=BTC, initial_quote=4.0)
        self.assertIsNone(pf.buy(80_000, 0))
        self.assertEqual(pf.quote, 4.0)

    def test_roundtrip_serialisation(self):
        pf = Portfolio(rules=BTC, initial_quote=40.0)
        pf.buy(80_000, 0)
        clone = Portfolio.from_dict(pf.to_dict())
        self.assertEqual(clone.to_dict(), pf.to_dict())


class StrategyTests(unittest.TestCase):
    def test_cross_signals(self):
        s = SmaCrossStrategy(2, 3)
        down = [candle(i, p) for i, p in enumerate([10, 9, 8, 7])]
        self.assertEqual(s.signal(down + [candle(4, 12)]), BUY)
        up = [candle(i, p) for i, p in enumerate([7, 8, 9, 10])]
        self.assertEqual(s.signal(up + [candle(4, 5)]), SELL)


class EngineTests(unittest.TestCase):
    def make_bot(self, **risk):
        return PaperBot(Portfolio(rules=BTC, initial_quote=40.0), SmaCrossStrategy(2, 3), RiskConfig(**risk))

    def test_stop_loss_in_backtest(self):
        prices = [100, 99, 98, 97, 110, 111]
        candles = [candle(i, p) for i, p in enumerate(prices)]
        candles.append(candle(6, 108, low=100))  # touche le stop à -2 %
        rules = SymbolRules("XUSDT", "X", "USDT", step_size=0.001, min_notional=5)
        bot = PaperBot(Portfolio(rules=rules, initial_quote=40.0), SmaCrossStrategy(2, 3), RiskConfig(2.0, 0))
        report = backtest(bot, candles)
        reasons = [f.reason for f in bot.portfolio.fills]
        self.assertEqual(reasons[-1], "stop-loss")
        self.assertEqual(report.round_trips, 1)
        self.assertLess(report.net_pnl, 0)

    def test_max_drawdown(self):
        self.assertAlmostEqual(max_drawdown_pct([40, 50, 25, 60]), 50.0)

    def test_live_loop_with_fake_client_persists_and_resumes(self):
        class FakeClient:
            prices = iter([100, 100, 100, 100, 130, 130])
            def quote(self, symbol):
                p = next(self.prices)
                return Quote(p, p, 10_000_000)
            def klines(self, symbol, interval, limit):
                closes = [10, 9, 8, 7, 12]
                return [candle(i, c) for i, c in enumerate(closes)]

        rules = SymbolRules("XUSDT", "X", "USDT", step_size=0.001, min_notional=5)
        bot = PaperBot(Portfolio(rules=rules, initial_quote=40.0), SmaCrossStrategy(2, 3), RiskConfig(2.0, 4.0))
        with tempfile.TemporaryDirectory() as d:
            store = RunStore(Path(d))
            meta = {"symbol": "XUSDT", "interval": "1m"}
            run_live(bot, FakeClient(), store, meta, poll_seconds=0, max_iterations=1, log=lambda *_: None)
            self.assertTrue(bot.portfolio.in_position)  # achat sur croisement
            bot2, _ = store.load()
            self.assertEqual(bot2.portfolio.to_dict(), bot.portfolio.to_dict())
            self.assertIn("BUY", store.trades_file.read_text())


class ReadOnlyTests(unittest.TestCase):
    def test_private_endpoints_are_refused(self):
        client = PublicMarketClient()
        for path in ("/api/v3/order", "/api/v3/account", "/sapi/v1/capital/withdraw/apply"):
            with self.assertRaises(PermissionError):
                client._get(path)

    def test_no_api_key_anywhere(self):
        src = "".join(p.read_text() for p in Path("paper_bot").glob("*.py"))
        for needle in ("X-MBX-APIKEY", "import hmac", "signature=", "method=\"POST\"", "method=\"DELETE\""):
            self.assertNotIn(needle, src)


if __name__ == "__main__":
    unittest.main()
