"""Stratégies : reçoivent les bougies CLÔTURÉES et renvoient un signal."""

from __future__ import annotations

from dataclasses import dataclass

from .market import Candle

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


def sma(values: list[float], n: int) -> float:
    return sum(values[-n:]) / n


@dataclass
class SmaCrossStrategy:
    """Achète quand la moyenne courte croise au-dessus de la longue, vend au croisement inverse.

    Stop-loss et take-profit sont gérés par le moteur (vérifiés à chaque prix).
    """

    fast: int = 9
    slow: int = 21

    def __post_init__(self):
        if not 0 < self.fast < self.slow:
            raise ValueError("Il faut 0 < fast < slow")

    @property
    def warmup(self) -> int:
        return self.slow + 1

    @property
    def name(self) -> str:
        return f"SMA{self.fast}/{self.slow}"

    def signal(self, candles: list[Candle]) -> str:
        if len(candles) < self.warmup:
            return HOLD
        closes = [c.close for c in candles]
        f_now, s_now = sma(closes, self.fast), sma(closes, self.slow)
        f_prev, s_prev = sma(closes[:-1], self.fast), sma(closes[:-1], self.slow)
        if f_prev <= s_prev and f_now > s_now:
            return BUY
        if f_prev >= s_prev and f_now < s_now:
            return SELL
        return HOLD
