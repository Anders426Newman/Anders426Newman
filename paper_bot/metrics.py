"""Mesure de performance d'une session de paper trading."""

from __future__ import annotations

from dataclasses import dataclass

from .portfolio import Portfolio


@dataclass
class Report:
    initial: float
    final_equity: float
    net_pnl: float
    return_pct: float
    gross_pnl: float  # PnL avant frais
    fees_paid: float
    fees_pct_of_capital: float
    buy_and_hold_pct: float
    max_drawdown_pct: float
    round_trips: int
    wins: int
    win_rate_pct: float
    avg_win: float
    avg_loss: float
    profit_factor: float | None
    exposure_pct: float
    open_position: bool
    start_ms: int
    end_ms: int

    def format(self) -> str:
        hours = (self.end_ms - self.start_ms) / 3_600_000
        pf = "n/a" if self.profit_factor is None else f"{self.profit_factor:.2f}"
        lines = [
            "=" * 46,
            " RAPPORT DE PERFORMANCE (argent virtuel)",
            "=" * 46,
            f" Durée observée        : {hours:,.1f} h",
            f" Capital initial       : {self.initial:,.4f} USDT",
            f" Valeur finale         : {self.final_equity:,.4f} USDT",
            f" PnL net               : {self.net_pnl:+,.4f} USDT ({self.return_pct:+.2f} %)",
            f" PnL brut (sans frais) : {self.gross_pnl:+,.4f} USDT",
            f" Frais payés           : {self.fees_paid:,.4f} USDT ({self.fees_pct_of_capital:.2f} % du capital)",
            f" Buy & hold (référence): {self.buy_and_hold_pct:+.2f} %",
            f" Drawdown max          : {self.max_drawdown_pct:.2f} %",
            f" Trades aller-retour   : {self.round_trips} (gagnants : {self.wins}, {self.win_rate_pct:.1f} %)",
            f" Gain moyen / perte moy: {self.avg_win:+.4f} / {self.avg_loss:+.4f} USDT",
            f" Profit factor         : {pf}",
            f" Temps investi         : {self.exposure_pct:.1f} %",
            f" Position ouverte      : {'oui' if self.open_position else 'non'}",
            "=" * 46,
        ]
        return "\n".join(lines)


def max_drawdown_pct(equities: list[float]) -> float:
    peak, worst = float("-inf"), 0.0
    for e in equities:
        peak = max(peak, e)
        if peak > 0:
            worst = max(worst, (peak - e) / peak)
    return worst * 100


def build_report(pf: Portfolio, curve: list[tuple[int, float, float]], first_price: float) -> Report:
    """`curve` : liste de (timestamp_ms, valeur du portefeuille, prix)."""
    if not curve:
        raise ValueError("Aucun point de prix enregistré")
    last_t, _, last_price = curve[-1]
    final = pf.equity(last_price)
    net = final - pf.initial_quote
    pnls = [f.pnl_usdt for f in pf.fills if f.side == "SELL" and f.pnl_usdt is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_loss = -sum(losses)
    exposure = _exposure(pf, curve)
    return Report(
        initial=pf.initial_quote,
        final_equity=final,
        net_pnl=net,
        return_pct=net / pf.initial_quote * 100,
        gross_pnl=net + pf.fees_paid,
        fees_paid=pf.fees_paid,
        fees_pct_of_capital=pf.fees_paid / pf.initial_quote * 100,
        buy_and_hold_pct=(last_price / first_price - 1) * 100 if first_price else 0.0,
        max_drawdown_pct=max_drawdown_pct([e for _, e, _ in curve]),
        round_trips=len(pnls),
        wins=len(wins),
        win_rate_pct=len(wins) / len(pnls) * 100 if pnls else 0.0,
        avg_win=sum(wins) / len(wins) if wins else 0.0,
        avg_loss=sum(losses) / len(losses) if losses else 0.0,
        profit_factor=(sum(wins) / gross_loss) if gross_loss > 0 else None,
        exposure_pct=exposure,
        open_position=pf.in_position,
        start_ms=curve[0][0],
        end_ms=last_t,
    )


def _exposure(pf: Portfolio, curve: list[tuple[int, float, float]]) -> float:
    start, end = curve[0][0], curve[-1][0]
    if end <= start:
        return 0.0
    invested, opened = 0, None
    for f in pf.fills:
        if f.side == "BUY" and opened is None:
            opened = f.time_ms
        elif f.side == "SELL" and opened is not None:
            invested += f.time_ms - opened
            opened = None
    if opened is not None:
        invested += end - opened
    return min(100.0, invested / (end - start) * 100)
