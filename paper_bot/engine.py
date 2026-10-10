"""Moteur commun au mode live (prix réels en direct) et au backtest (historique réel)."""

from __future__ import annotations

import csv
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .market import Candle, PublicMarketClient, Quote
from .metrics import Report, build_report
from .portfolio import Fill, Portfolio
from .strategy import BUY, SELL, SmaCrossStrategy


@dataclass
class RiskConfig:
    stop_loss_pct: float = 2.0  # 0 = désactivé
    take_profit_pct: float = 4.0  # 0 = désactivé
    position_fraction: float = 1.0  # part de l'USDT engagée à chaque achat


@dataclass
class PaperBot:
    portfolio: Portfolio
    strategy: SmaCrossStrategy
    risk: RiskConfig = field(default_factory=RiskConfig)
    curve: list[tuple[int, float, float]] = field(default_factory=list)
    first_price: float = 0.0
    last_candle_open: int = 0
    on_fill: object = None  # callback(Fill) optionnel

    # -- niveaux de sortie ------------------------------------------------------
    def stop_price(self) -> float | None:
        if not self.portfolio.in_position or self.risk.stop_loss_pct <= 0:
            return None
        return self.portfolio.entry_price * (1 - self.risk.stop_loss_pct / 100)

    def take_price(self) -> float | None:
        if not self.portfolio.in_position or self.risk.take_profit_pct <= 0:
            return None
        return self.portfolio.entry_price * (1 + self.risk.take_profit_pct / 100)

    # -- événements -------------------------------------------------------------
    def _record(self, fill: Fill | None) -> Fill | None:
        if fill and callable(self.on_fill):
            self.on_fill(fill)
        return fill

    def mark(self, time_ms: int, price: float) -> None:
        if not self.first_price:
            self.first_price = price
        self.curve.append((time_ms, self.portfolio.equity(price), price))

    def check_exits(self, bid: float, time_ms: int) -> Fill | None:
        """Stop-loss / take-profit évalués sur le prix vendeur courant."""
        sp, tp = self.stop_price(), self.take_price()
        if sp is not None and bid <= sp:
            return self._record(self.portfolio.sell(bid, time_ms, "stop-loss"))
        if tp is not None and bid >= tp:
            return self._record(self.portfolio.sell(bid, time_ms, "take-profit"))
        return None

    def on_closed_candles(self, candles: list[Candle], quote: Quote) -> Fill | None:
        """Appelé une fois par nouvelle bougie clôturée."""
        self.last_candle_open = candles[-1].open_time
        sig = self.strategy.signal(candles)
        pf = self.portfolio
        if sig == BUY and not pf.in_position:
            return self._record(pf.buy(quote.ask, quote.timestamp_ms, self.risk.position_fraction, f"signal {self.strategy.name}"))
        if sig == SELL and pf.in_position:
            return self._record(pf.sell(quote.bid, quote.timestamp_ms, f"signal {self.strategy.name}"))
        return None

    def report(self) -> Report:
        return build_report(self.portfolio, self.curve, self.first_price)


# ---------------------------------------------------------------------------
# Backtest sur bougies historiques réelles
# ---------------------------------------------------------------------------
def backtest(bot: PaperBot, candles: list[Candle]) -> Report:
    """Rejoue les bougies. Les ordres de signal s'exécutent au cours de clôture
    (+ slippage du portefeuille). Les stops sont testés en intra-bougie sur le
    plus bas / plus haut ; si les deux sont touchés, le stop est pris (prudent)."""
    w = bot.strategy.warmup
    for i, c in enumerate(candles):
        if bot.portfolio.in_position:
            sp, tp = bot.stop_price(), bot.take_price()
            if sp is not None and c.low <= sp:
                bot._record(bot.portfolio.sell(min(sp, c.open), c.close_time, "stop-loss"))
            elif tp is not None and c.high >= tp:
                bot._record(bot.portfolio.sell(max(tp, c.open), c.close_time, "take-profit"))
        if i + 1 >= w:
            bot.on_closed_candles(candles[i + 1 - w : i + 1], Quote(c.close, c.close, c.close_time))
        bot.mark(c.close_time, c.close)
    return bot.report()


# ---------------------------------------------------------------------------
# Mode live : prix réels Binance, ordres simulés, état persistant
# ---------------------------------------------------------------------------
def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class RunStore:
    """Dossier d'une session : state.json, trades.csv, equity.csv."""

    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.dir / "state.json"
        self.trades_file = self.dir / "trades.csv"
        self.equity_file = self.dir / "equity.csv"

    def exists(self) -> bool:
        return self.state_file.exists()

    def save(self, bot: PaperBot, meta: dict) -> None:
        state = {
            "meta": meta,
            "portfolio": bot.portfolio.to_dict(),
            "risk": asdict(bot.risk),
            "strategy": {"fast": bot.strategy.fast, "slow": bot.strategy.slow},
            "first_price": bot.first_price,
            "last_candle_open": bot.last_candle_open,
        }
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(self.state_file)

    def load(self) -> tuple[PaperBot, dict]:
        state = json.loads(self.state_file.read_text())
        bot = PaperBot(
            portfolio=Portfolio.from_dict(state["portfolio"]),
            strategy=SmaCrossStrategy(**state["strategy"]),
            risk=RiskConfig(**state["risk"]),
            first_price=state["first_price"],
            last_candle_open=state["last_candle_open"],
        )
        bot.curve = self.read_curve()
        return bot, state["meta"]

    def append_trade(self, f: Fill) -> None:
        new = not self.trades_file.exists()
        with self.trades_file.open("a", newline="") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["time_utc", "side", "price", "qty", "notional_usdt", "fee_usdt", "fee_asset", "pnl_usdt", "reason"])
            w.writerow([_iso(f.time_ms), f.side, f"{f.price:.8f}", f"{f.qty:.8f}", f"{f.notional:.6f}",
                        f"{f.fee_usdt:.6f}", f.fee_asset, "" if f.pnl_usdt is None else f"{f.pnl_usdt:.6f}", f.reason])

    def append_equity(self, t: int, equity: float, price: float) -> None:
        new = not self.equity_file.exists()
        with self.equity_file.open("a", newline="") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["time_ms", "time_utc", "equity_usdt", "price"])
            w.writerow([t, _iso(t), f"{equity:.6f}", f"{price:.8f}"])

    def read_curve(self) -> list[tuple[int, float, float]]:
        if not self.equity_file.exists():
            return []
        with self.equity_file.open() as fh:
            return [(int(r["time_ms"]), float(r["equity_usdt"]), float(r["price"])) for r in csv.DictReader(fh)]


def run_live(bot: PaperBot, client: PublicMarketClient, store: RunStore, meta: dict,
             poll_seconds: float = 15.0, max_iterations: int | None = None, log=print) -> Report:
    symbol, interval = meta["symbol"], meta["interval"]
    bot.on_fill = lambda f: (store.append_trade(f), log(
        f"[{_iso(f.time_ms)}] {f.side:4} {f.qty:.8f} @ {f.price:,.2f}  frais {f.fee_usdt:.4f} USDT"
        + ("" if f.pnl_usdt is None else f"  PnL {f.pnl_usdt:+.4f} USDT") + f"  ({f.reason})"))
    n = 0
    try:
        while max_iterations is None or n < max_iterations:
            n += 1
            try:
                quote = client.quote(symbol)
                bot.check_exits(quote.bid, quote.timestamp_ms)
                candles = client.klines(symbol, interval, limit=bot.strategy.warmup + 1)
                closed = [c for c in candles if c.close_time < quote.timestamp_ms]
                if closed and closed[-1].open_time > bot.last_candle_open:
                    bot.on_closed_candles(closed, quote)
                bot.mark(quote.timestamp_ms, quote.bid)
                store.append_equity(*bot.curve[-1])
                store.save(bot, meta)
                pf = bot.portfolio
                log(f"[{_iso(quote.timestamp_ms)}] {symbol} bid {quote.bid:,.2f} ask {quote.ask:,.2f} | "
                    f"USDT {pf.quote:.4f} {pf.rules.base_asset} {pf.base:.8f} | valeur {bot.curve[-1][1]:.4f} USDT")
            except Exception as exc:  # réseau, etc. : on continue au prochain tour
                log(f"Erreur (on réessaie au prochain tour) : {exc}")
            if max_iterations is None or n < max_iterations:
                time.sleep(poll_seconds)
    except KeyboardInterrupt:
        log("\nArrêt demandé.")
    store.save(bot, meta)
    return bot.report()
