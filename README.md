# Prediction Market Alpha Research

This repository is the research and paper-trading foundation for evaluating
repeatable alpha in prediction markets.

The system is deliberately separated into:

- **Market data**: normalized, replayable snapshots and trades.
- **Signals**: strategy logic that emits order intents without placing orders.
- **Execution simulation**: conservative fill, fee, latency, and capital models.
- **Evaluation**: reproducible metrics and out-of-sample reports.
- **Live adapters**: disabled until paper-trading promotion gates are met.

The first implementation provides a small event-driven replay core. It does
not connect to an exchange or place orders.

## Continuous Kalshi paper capture

The repository includes an unauthenticated market-data collector for Kalshi.
It stores normalized top-of-book snapshots in SQLite and intentionally emits
no orders:

```powershell
python run_kalshi_paper.py KX-MARKET-TICKER --db data\market_data.sqlite
```

To refresh the tracked universe from newly opened ordinary binary markets:

```powershell
python run_kalshi_paper.py KX-MARKET-TICKER --discover --db data\market_data.sqlite
```

The discovery mode is still public-data paper capture. It does not identify
whether a Kalshi contract is legally identical to a sportsbook line; that
requires an authorized reference feed and manual contract-rule verification.
The collector backs off on Kalshi rate limits, limits concurrent requests, and
skips an individual unavailable ticker without stopping the service.

## Live-data paper trading

The same Oracle process now runs four no-money strategies against each fetched
snapshot:

- threshold buying
- momentum
- mean reversion
- stable high probability (the 68–72 cent stability proxy)

Orders are simulated locally against the observed top-of-book only. The
portfolio records `paper_orders`, `paper_fills`, `paper_positions`, and
`paper_equity` in the same SQLite database. It never calls a Kalshi order
endpoint. The service log should contain:

```text
PAPER MODE ONLY: intents are simulated against snapshots; no live orders are sent
```

The live paper balance defaults to $100 with a 1% research fee assumption.
This is an execution simulation, not a guarantee of fills: it does not model
queue position, hidden liquidity, or exchange acknowledgement latency. Check
the current paper activity over SSH:

```bash
sudo python3 - <<'PY'
import sqlite3
c = sqlite3.connect("/var/lib/pm-alpha/market_data.sqlite")
for table in ("paper_orders", "paper_fills", "paper_positions", "paper_equity"):
    print(table, c.execute("select count(*) from " + table).fetchone()[0])
print(c.execute(
    "select signal, status, count(*) from paper_orders group by signal, status"
).fetchall())
print(c.execute(
    "select timestamp_ms, cash, positions_value, equity, fees "
    "from paper_equity order by id desc limit 1"
).fetchone())
PY
```

## External-reference market-making research

Reference prices must be exported from an authorized sportsbook or venue feed
after removing vig and checking settlement rules. Use one CSV row per update:

```text
timestamp_ms,market_id,fair_probability,source
1760000000000,KX-MARKET-TICKER,0.57,authorized_feed
```

Run the conservative quote simulation against captured Kalshi snapshots:

```powershell
python run_market_maker_backtest.py `
  --snapshots data\kalshi.csv `
  --references data\references.csv `
  --half-spread 0.03 `
  --max-inventory 5 `
  --fee-rate 0.01 `
  --adverse-selection-bps 5
```

This simulator only fills when the observed book crosses the quote. It does
not assume queue priority, guaranteed fills, or that similar contract titles
have identical settlement rules. Positive results from synthetic candles or
unverified line matches are not evidence of a live edge.

## Why copy the SQLite database?

The Oracle process can perform forward paper trading continuously and can
produce online counters. Copying the database is only needed for retrospective
analysis: walk-forward splits, market-by-market comparisons, parameter sweeps,
and debugging data-quality problems. The database is not proof of fills or
profitability by itself. In particular, a capture with empty books, sparse
quotes, or no settlement records cannot validate a strategy.

On Windows, the repository includes a one-command sync helper. It stops the
collector briefly, copies the SQLite file consistently, restarts the service,
downloads the copy, and optionally runs the diagnostics and baseline sweep:

```powershell
.\deploy\pull_oracle_data.ps1 `
  -SshKey "C:\path\to\oracle.key" `
  -OracleHost "157.151.132.129" `
  -RunAnalysis
```

The default local output is `data\oracle_market_data.sqlite`. Use `-Output`
to keep dated copies, for example
`data\oracle_2026-09-25.sqlite`.

WSL is not required. It can be useful if you prefer Linux tooling, but native
Windows OpenSSH (`ssh` and `scp`) is sufficient. The Oracle database is kept
separate because it is mutable runtime state and can grow continuously; the
repository contains reproducible code and analysis, while the helper bridges
the two without committing credentials or multi-gigabyte database files.

This is a data-capture and paper-trading primitive, not evidence of
profitability. A strategy must pass realistic fee, fill, latency, out-of-sample,
and forward paper-trading gates before any live adapter is considered.

## Current safety boundary

There is no live order adapter in this repository. The continuous entrypoint
only reads public market data and writes paper data. The backtester explicitly
models liquidity consumption, latency, fees, settlement, rejected orders,
unresolved inventory, and non-atomic paired execution. Results should be
treated as research measurements until they survive forward paper trading.

## Historical backtesting

For an execution-neutral first pass, use public Kalshi candlesticks:

```powershell
python run_historical_backtest.py `
  --ticker KX-MARKET-TICKER `
  --start-ts 1760000000 `
  --end-ts 1760086400 `
  --threshold 0.25 `
  --fee-rate 0.01
```

You can also replay a normalized CSV:

```powershell
python run_historical_backtest.py --csv snapshots.csv --fee-rate 0.01
```

The candlestick adapter creates a synthetic one-cent spread and one-contract
depth so directional signals can be compared consistently. It is explicitly
not valid evidence for latency arbitrage, market making, queue position, or
fill-rate claims. Those require the websocket/order-book capture generated by
the continuous paper collector.

The included threshold strategy is a diagnostic baseline, not a recommended
strategy. Run it across many markets and untouched time ranges; do not tune
the threshold on the final test period.

## Broad offline sweep

Once you have one or more normalized CSV files, compare the baseline families
and several fee assumptions in one run:

```powershell
python run_sweep.py data\market_a.csv data\market_b.csv --output data\sweep.csv
```

Or run directly against the SQLite database produced by the Oracle collector:

```powershell
python run_sweep.py --sqlite data\market_data.sqlite --output data\sweep.csv
```

Before interpreting a sweep, inspect whether the capture is large enough:

```powershell
python inspect_market_data.py data\oracle_market_data.sqlite
```

This reports per-market snapshot count, time range, quote coverage, average
spread, and displayed top-of-book size. A market with sparse snapshots or low
quote coverage should not be used to claim that a strategy works.

This deliberately runs threshold, momentum, and mean-reversion grids as
independent strategy instances. The output is a research ranking input, not a
live-trading recommendation. A high return on one market or one fee setting is
not sufficient; require performance across markets, chronological holdouts,
and execution-grade paper data.

The sweep also includes `stable_high_probability`, a proxy for the proposed
"boring event around 70 cents" idea. It buys once after several consecutive
observations remain in a narrow probability band. Because the normalized
snapshot schema does not contain market expiry, this is not yet a true
one-month strategy: expiry filtering must be supplied from Kalshi market
metadata before its results can be interpreted as long-dated behavior.

## Development

```powershell
python -m pip install -e ".[dev]"
python -m pytest
```

Do not place API keys or private keys in this repository. Use environment
variables or a local ignored configuration file for future venue adapters.

## Credentials

You do not need to provide credentials for the public historical and snapshot
collectors. If authenticated Kalshi features are added later, keep the key ID
and private key in an ignored local `.env`/file path. Never paste private keys
into chat, commit them, or enable live trading merely to run research.

## Oracle deployment

See [deploy/README.md](deploy/README.md) for an Ubuntu systemd deployment that
continues collecting public market data when your local computer is offline.
