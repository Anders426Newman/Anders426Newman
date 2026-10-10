"""Ligne de commande : python -m paper_bot {live,backtest,report} ..."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .engine import PaperBot, RiskConfig, RunStore, backtest, run_live
from .market import Candle, MarketDataError, PublicMarketClient, SymbolRules
from .portfolio import Portfolio
from .strategy import SmaCrossStrategy


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="5m", help="intervalle des bougies (1m, 5m, 15m, 1h...)")
    p.add_argument("--capital", type=float, default=40.0, help="USDT virtuels de départ")
    p.add_argument("--fee", type=float, default=0.1, help="frais par ordre en %% (Binance spot : 0.1)")
    p.add_argument("--fast", type=int, default=9)
    p.add_argument("--slow", type=int, default=21)
    p.add_argument("--stop-loss", type=float, default=2.0, help="%% sous le prix d'entrée (0 = off)")
    p.add_argument("--take-profit", type=float, default=4.0, help="%% au-dessus du prix d'entrée (0 = off)")
    p.add_argument("--fraction", type=float, default=1.0, help="part de l'USDT engagée par achat (0-1)")


def _rules(client: PublicMarketClient, symbol: str) -> SymbolRules:
    try:
        return client.symbol_rules(symbol)
    except MarketDataError as exc:
        print(f"Avertissement : filtres Binance indisponibles ({exc}), valeurs par défaut utilisées.", file=sys.stderr)
        return SymbolRules.fallback(symbol)


def _new_bot(args, rules: SymbolRules, slippage_bps: float) -> PaperBot:
    return PaperBot(
        portfolio=Portfolio(rules=rules, initial_quote=args.capital, fee_rate=args.fee / 100, slippage_bps=slippage_bps),
        strategy=SmaCrossStrategy(args.fast, args.slow),
        risk=RiskConfig(args.stop_loss, args.take_profit, args.fraction),
    )


def cmd_live(args) -> None:
    client = PublicMarketClient()
    store = RunStore(Path(args.run_dir))
    if store.exists() and not args.reset:
        bot, meta = store.load()
        print(f"Reprise de la session {store.dir} ({meta['symbol']} {meta['interval']}).")
    else:
        for f in (store.state_file, store.trades_file, store.equity_file):
            f.unlink(missing_ok=True)
        rules = _rules(client, args.symbol)
        bot = _new_bot(args, rules, args.slippage)
        meta = {"symbol": args.symbol, "interval": args.interval}
        print(f"Nouvelle session : {args.capital} USDT virtuels sur {args.symbol}, frais {args.fee} %, "
              f"stepSize {rules.step_size}, minNotional {rules.min_notional} USDT.")
    print("Mode SIMULATION : aucune clé API, aucun ordre réel. Ctrl+C pour arrêter.\n")
    report = run_live(bot, client, store, meta, args.poll, args.iterations)
    print(report.format())


def _load_candles(args, client: PublicMarketClient) -> list[Candle]:
    if args.file:
        rows = json.loads(Path(args.file).read_text())
        return [Candle.from_binance(r) for r in rows]
    return client.history(args.symbol, args.interval, args.candles)


def cmd_backtest(args) -> None:
    client = PublicMarketClient(retries=1)
    candles = _load_candles(args, client)
    rules = _rules(client, args.symbol) if not args.offline else SymbolRules.fallback(args.symbol)
    bot = _new_bot(args, rules, args.slippage)
    report = backtest(bot, candles)
    print(f"Backtest {args.symbol} {args.interval} sur {len(candles)} bougies réelles, stratégie {bot.strategy.name}")
    for f in bot.portfolio.fills:
        pnl = "" if f.pnl_usdt is None else f" PnL {f.pnl_usdt:+.4f}"
        print(f"  {f.side:4} {f.qty:.8f} @ {f.price:,.2f} frais {f.fee_usdt:.4f}{pnl} ({f.reason})")
    print(report.format())


def cmd_report(args) -> None:
    store = RunStore(Path(args.run_dir))
    if not store.exists():
        sys.exit(f"Aucune session dans {store.dir}")
    bot, meta = store.load()
    print(f"Session {store.dir} — {meta['symbol']} {meta['interval']}")
    print(bot.report().format())


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="paper_bot", description="Paper trading sur prix réels Binance (lecture seule).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    live = sub.add_parser("live", help="suivre les prix en direct et trader virtuellement")
    _common(live)
    live.add_argument("--run-dir", default="runs/default")
    live.add_argument("--poll", type=float, default=15.0, help="secondes entre deux relevés de prix")
    live.add_argument("--iterations", type=int, default=None, help="nombre de relevés (défaut : infini)")
    live.add_argument("--slippage", type=float, default=0.0, help="glissement en points de base, en plus du bid/ask réel")
    live.add_argument("--reset", action="store_true", help="repartir de zéro en écrasant la session")
    live.set_defaults(func=cmd_live)

    bt = sub.add_parser("backtest", help="rejouer l'historique réel de Binance")
    _common(bt)
    bt.add_argument("--candles", type=int, default=2000, help="nombre de bougies à télécharger")
    bt.add_argument("--file", help="fichier JSON de klines Binance (au lieu du téléchargement)")
    bt.add_argument("--offline", action="store_true", help="ne pas interroger exchangeInfo")
    bt.add_argument("--slippage", type=float, default=2.0, help="glissement en points de base (simule le spread)")
    bt.set_defaults(func=cmd_backtest)

    rep = sub.add_parser("report", help="afficher les performances d'une session live")
    rep.add_argument("--run-dir", default="runs/default")
    rep.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
