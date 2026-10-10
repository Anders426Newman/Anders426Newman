"""Portefeuille virtuel : exécution simulée, frais et filtres Binance."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from .market import SymbolRules


def floor_to_step(qty: float, step: float) -> float:
    if step <= 0:
        return qty
    decimals = max(0, -int(math.floor(math.log10(step))))
    return round(math.floor(qty / step + 1e-9) * step, decimals)


@dataclass
class Fill:
    time_ms: int
    side: str  # BUY / SELL
    price: float
    qty: float  # quantité de base exécutée (avant frais)
    notional: float  # montant en quote (USDT)
    fee_usdt: float  # frais convertis en USDT
    fee_asset: str
    reason: str
    pnl_usdt: float | None = None  # PnL net du trade aller-retour (sur les ventes)


@dataclass
class Portfolio:
    """Spot uniquement, une seule paire, pas de levier.

    Frais Binance spot : 0,1 % par défaut (maker comme taker). Sans BNB, les frais
    d'un achat sont prélevés sur l'actif reçu (BTC), ceux d'une vente sur l'USDT
    reçu ; c'est ce qui est modélisé ici, y compris la « poussière » qui reste
    après arrondi au stepSize.
    """

    rules: SymbolRules
    initial_quote: float = 40.0
    fee_rate: float = 0.001
    slippage_bps: float = 0.0
    quote: float = field(default=None)  # type: ignore[assignment]
    base: float = 0.0
    entry_cost: float = 0.0  # USDT dépensés (frais compris) pour la position ouverte
    entry_price: float = 0.0
    fees_paid: float = 0.0
    fills: list[Fill] = field(default_factory=list)

    def __post_init__(self):
        if self.quote is None:
            self.quote = self.initial_quote

    # -- état -------------------------------------------------------------
    @property
    def in_position(self) -> bool:
        # entry_price est remis à 0 à chaque vente : la poussière restante
        # (inférieure au stepSize ou au minNotional) ne compte pas comme une position.
        return self.entry_price > 0 and self.tradable_qty() > 0

    def tradable_qty(self, price: float | None = None) -> float:
        qty = floor_to_step(self.base, self.rules.step_size)
        if qty < self.rules.min_qty:
            return 0.0
        if price is not None and qty * price < self.rules.min_notional:
            return 0.0
        return qty

    def equity(self, price: float) -> float:
        return self.quote + self.base * price

    # -- exécution ----------------------------------------------------------
    def _slip(self, price: float, side: str) -> float:
        k = self.slippage_bps / 10_000
        return price * (1 + k) if side == "BUY" else price * (1 - k)

    def buy(self, ask: float, time_ms: int, fraction: float = 1.0, reason: str = "") -> Fill | None:
        price = self._slip(ask, "BUY")
        budget = self.quote * min(max(fraction, 0.0), 1.0)
        qty = floor_to_step(budget / price, self.rules.step_size)
        notional = qty * price
        if qty <= 0 or qty < self.rules.min_qty or notional < self.rules.min_notional:
            return None
        fee_base = qty * self.fee_rate
        fee_usdt = fee_base * price
        self.quote -= notional
        self.base += qty - fee_base
        self.entry_cost += notional
        self.entry_price = self.entry_cost / self.base if self.base else price
        self.fees_paid += fee_usdt
        fill = Fill(time_ms, "BUY", price, qty, notional, fee_usdt, self.rules.base_asset, reason)
        self.fills.append(fill)
        return fill

    def sell(self, bid: float, time_ms: int, reason: str = "") -> Fill | None:
        price = self._slip(bid, "SELL")
        qty = self.tradable_qty(price)
        if qty <= 0:
            return None
        notional = qty * price
        fee_usdt = notional * self.fee_rate
        proceeds = notional - fee_usdt
        # coût d'entrée au prorata de la quantité vendue (la poussière garde le reste)
        cost = self.entry_cost * (qty / self.base) if self.base else 0.0
        pnl = proceeds - cost
        self.quote += proceeds
        self.base = round(self.base - qty, 12)
        self.entry_cost -= cost
        self.entry_price = 0.0
        self.fees_paid += fee_usdt
        fill = Fill(time_ms, "SELL", price, qty, notional, fee_usdt, self.rules.quote_asset, reason, pnl)
        self.fills.append(fill)
        return fill

    # -- persistance ----------------------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        d["rules"] = asdict(self.rules)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Portfolio":
        d = dict(d)
        d["rules"] = SymbolRules(**d["rules"])
        d["fills"] = [Fill(**f) for f in d.get("fills", [])]
        return cls(**d)
