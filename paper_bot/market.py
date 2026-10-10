"""Client Binance en LECTURE SEULE.

Ce module ne connaît que des endpoints publics de données de marché, appelés en
GET, sans clé API ni signature. Il est donc techniquement incapable de passer un
ordre, d'annuler un ordre ou de lire/modifier le compte réel.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

# data-api.binance.vision ne sert QUE des données de marché publiques : même avec
# une erreur de code, aucun ordre ne pourrait y être envoyé.
DEFAULT_BASE_URLS = ("https://data-api.binance.vision", "https://api.binance.com")

# Liste blanche stricte : toute autre route est refusée avant l'appel réseau.
ALLOWED_PATHS = frozenset(
    {
        "/api/v3/ping",
        "/api/v3/time",
        "/api/v3/exchangeInfo",
        "/api/v3/ticker/price",
        "/api/v3/ticker/bookTicker",
        "/api/v3/klines",
    }
)


class MarketDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float
    timestamp_ms: int

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


@dataclass(frozen=True)
class Candle:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int

    @classmethod
    def from_binance(cls, row: list) -> "Candle":
        return cls(
            open_time=int(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
            close_time=int(row[6]),
        )


@dataclass(frozen=True)
class SymbolRules:
    """Filtres d'échange qui rendent la simulation réaliste avec un petit capital."""

    symbol: str
    base_asset: str
    quote_asset: str
    step_size: float = 1e-8
    min_qty: float = 0.0
    min_notional: float = 5.0

    @classmethod
    def from_exchange_info(cls, info: dict) -> "SymbolRules":
        sym = info["symbols"][0]
        rules = {"step_size": 1e-8, "min_qty": 0.0, "min_notional": 5.0}
        for f in sym.get("filters", []):
            if f["filterType"] == "LOT_SIZE":
                rules["step_size"] = float(f["stepSize"])
                rules["min_qty"] = float(f["minQty"])
            elif f["filterType"] in ("NOTIONAL", "MIN_NOTIONAL"):
                rules["min_notional"] = float(f["minNotional"])
        return cls(
            symbol=sym["symbol"],
            base_asset=sym["baseAsset"],
            quote_asset=sym["quoteAsset"],
            **rules,
        )

    @classmethod
    def fallback(cls, symbol: str, quote_asset: str = "USDT") -> "SymbolRules":
        base = symbol[: -len(quote_asset)] if symbol.endswith(quote_asset) else symbol
        return cls(symbol=symbol, base_asset=base, quote_asset=quote_asset)


class PublicMarketClient:
    """Accès HTTP GET aux seules données publiques de Binance."""

    def __init__(self, base_urls=DEFAULT_BASE_URLS, timeout: float = 10.0, retries: int = 3):
        self.base_urls = tuple(base_urls)
        self.timeout = timeout
        self.retries = retries

    def _get(self, path: str, params: dict | None = None):
        if path not in ALLOWED_PATHS:
            raise PermissionError(f"Endpoint non autorisé en mode simulation : {path}")
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        last_error: Exception | None = None
        for attempt in range(self.retries):
            for base in self.base_urls:
                req = urllib.request.Request(
                    base + path + query,
                    method="GET",
                    headers={"User-Agent": "paper-bot/0.1"},
                )
                try:
                    with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                        return json.loads(resp.read().decode())
                except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                    last_error = exc
            time.sleep(2**attempt)
        raise MarketDataError(f"Binance injoignable pour {path}: {last_error}")

    def symbol_rules(self, symbol: str) -> SymbolRules:
        return SymbolRules.from_exchange_info(self._get("/api/v3/exchangeInfo", {"symbol": symbol}))

    def quote(self, symbol: str) -> Quote:
        data = self._get("/api/v3/ticker/bookTicker", {"symbol": symbol})
        return Quote(bid=float(data["bidPrice"]), ask=float(data["askPrice"]), timestamp_ms=int(time.time() * 1000))

    def klines(self, symbol: str, interval: str, limit: int = 500, start_time: int | None = None) -> list[Candle]:
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_time is not None:
            params["startTime"] = start_time
        return [Candle.from_binance(r) for r in self._get("/api/v3/klines", params)]

    def history(self, symbol: str, interval: str, total: int) -> list[Candle]:
        """Récupère jusqu'à `total` bougies récentes en paginant par 1000."""
        candles = self.klines(symbol, interval, limit=min(total, 1000))
        while len(candles) < total:
            span = candles[1].open_time - candles[0].open_time if len(candles) > 1 else 60_000
            need = min(total - len(candles), 1000)
            older = self.klines(symbol, interval, limit=need, start_time=candles[0].open_time - need * span)
            older = [c for c in older if c.open_time < candles[0].open_time]
            if not older:
                break
            candles = older + candles
        return candles
