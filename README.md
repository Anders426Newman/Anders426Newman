# Paper bot Binance : 40 USDT virtuels

Ce bot suit les **vrais prix Binance**, simule des achats et des ventes avec **40 USDT virtuels**,
compte les **frais** et mesure les **performances**. Il ne passe **aucun ordre réel**.

## Sécurité : aucun accès au compte réel

- Aucune clé API n'est demandée, lue ni stockée.
- Le client (`paper_bot/market.py`) n'envoie que des requêtes `GET` vers des endpoints publics
  de données de marché, listés dans une liste blanche (`ticker`, `bookTicker`, `klines`,
  `exchangeInfo`). Toute autre route (`/api/v3/order`, `/api/v3/account`…) est refusée avant
  tout appel réseau. Un test unitaire le vérifie.
- Hôte utilisé en priorité : `data-api.binance.vision`, qui ne sert que des données publiques
  et ne peut pas recevoir d'ordres. `api.binance.com` sert de secours.

## Installation

Python 3.10 ou plus récent suffit. Le bot n'utilise que la bibliothèque standard, il n'y a donc rien à installer.

## Utilisation

### 1. Trading virtuel en direct (prix réels)

```bash
python -m paper_bot live                       # BTCUSDT, bougies 5m, 40 USDT
python -m paper_bot live --symbol ETHUSDT --interval 15m --run-dir runs/eth
```

Toutes les 15 secondes, le bot lit le meilleur bid et ask réel (`bookTicker`). Les achats
virtuels s'exécutent à l'ask et les ventes au bid, donc le spread réel est payé. La session est
sauvegardée dans `runs/<nom>/` :

| Fichier       | Contenu                                         |
|---------------|-------------------------------------------------|
| `state.json`  | état du portefeuille, pour reprendre après Ctrl+C |
| `trades.csv`  | chaque ordre simulé : prix, quantité, frais, PnL |
| `equity.csv`  | valeur du portefeuille à chaque relevé          |

Si vous relancez la même commande, la session reprend là où elle s'était arrêtée. `--reset` repart de zéro.

### 2. Rapport de performance d'une session

```bash
python -m paper_bot report --run-dir runs/default
```

### 3. Backtest sur l'historique réel

```bash
python -m paper_bot backtest --candles 3000
python -m paper_bot backtest --file klines.json --offline   # klines Binance déjà téléchargées
```

## Ce qui est simulé

- **Frais Binance spot : 0,1 %** par ordre (`--fee`). Sur un achat, ils sont prélevés en BTC,
  et sur une vente, en USDT, comme sur Binance sans réduction BNB. Avec la réduction BNB,
  utilisez `--fee 0.075`.
- **Filtres réels de la paire** (`exchangeInfo`) : la quantité est arrondie au `stepSize` et
  le montant d'un ordre doit être d'au moins `minNotional` (5 USDT). La « poussière » qui reste
  après arrondi est conservée, comme sur un vrai compte.
- **Spread et glissement** : en live, on paie le bid/ask réel (`--slippage` en option). En
  backtest, on n'a que les prix de clôture, donc `--slippage 2` (2 points de base) est
  appliqué par défaut.

## Stratégie par défaut

Croisement de moyennes mobiles **SMA 9 / SMA 21** sur bougies clôturées, avec **stop-loss −2 %**
et **take-profit +4 %**. Tout est réglable : `--fast`, `--slow`, `--stop-loss`, `--take-profit`
et `--fraction` (part du capital engagée).

Pour essayer une autre stratégie, écrivez une classe avec `warmup`, `name` et
`signal(candles) -> "BUY" | "SELL" | "HOLD"` (voir `paper_bot/strategy.py`).

## Indicateurs mesurés

Capital final, PnL net, PnL brut avant frais, total des frais (en USDT et en % du capital),
comparaison avec le buy & hold, drawdown maximum, nombre de trades, taux de réussite,
gain et perte moyens, profit factor et temps investi.

## Tests

```bash
python -m unittest discover -s tests -t .
```
