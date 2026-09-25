# PMAlphaGenerationIdeas Handoff Context

This document is a handoff for another engineering/research agent. It
describes the repository, Oracle VM deployment, current paper-trading state,
strategies, testing history, known limitations, and safe next steps.

## Project purpose

The project evaluates whether repeatable, risk-adjusted alpha can be generated
in prediction markets, initially using Kalshi public market data. It is
research and paper trading only. There is no live order adapter and no code
should place real orders without an explicit, separately reviewed change.

The intended workflow is:

1. Discover or select markets.
2. Poll public Kalshi order books.
3. Normalize snapshots into a common model.
4. Persist snapshots and simulated portfolio activity in SQLite.
5. Run multiple strategies concurrently in paper mode.
6. Compare realized/mark-to-market performance, fees, fills, inventory, and
   drawdowns over sufficiently long forward and historical windows.
7. Only consider live trading after robust out-of-sample and forward paper
   evidence. No such evidence exists yet.

## Repository and important paths

Repository:

```text
C:\Users\Will Palaia\Downloads\dev\PMAlphaGenerationIdeas\PMAlphaGenerationIdeas
```

GitHub:

```text
https://github.com/WillPalaia/PMAlphaGenerationIdeas
```

Important entry points:

- `run_kalshi_paper.py`: continuous public-data collector and live-data paper
  portfolio runner.
- `run_sweep.py`: offline baseline strategy/fee sweep over CSV or SQLite data.
- `run_historical_backtest.py`: public Kalshi candlestick diagnostic replay.
- `run_market_maker_backtest.py`: external-reference market-making simulator.
- `inspect_market_data.py`: SQLite data-quality/coverage report.
- `deploy/pull_oracle_data.ps1`: one-command Windows database sync and optional
  analysis.
- `deploy/pm-alpha-paper.service`: systemd unit template.
- `deploy/install_oracle_paper.sh`: initial Ubuntu/Oracle installer.

Core modules:

- `src/pm_alpha/models.py`: `MarketSnapshot`, `OrderIntent`, `Fill`,
  `BacktestResult`, `Side`.
- `src/pm_alpha/kalshi_source.py`: public Kalshi order-book adapter.
- `src/pm_alpha/discovery.py`: ordinary binary market discovery.
- `src/pm_alpha/storage.py`: SQLite schema and snapshot persistence.
- `src/pm_alpha/paper.py`: paper runner, `MultiStrategy`, `PaperPortfolio`,
  simulated fills, positions, settlements, and equity.
- `src/pm_alpha/backtest.py`: event-driven historical replay with latency,
  liquidity, fees, settlement, and unresolved inventory.
- `src/pm_alpha/strategies.py`: baseline directional strategies and the
  stable-high-probability strategy.
- `src/pm_alpha/market_making.py`: conservative external-reference quote
  simulator.
- `src/pm_alpha/sweep.py`: parameter/fee grid.
- `src/pm_alpha/metrics.py`: basic return, fee, fill, and unresolved-inventory
  metrics.
- `src/pm_alpha/evaluation.py`: chronological walk-forward windows.
- `src/pm_alpha/paired.py`: non-atomic paired-leg stress simulation.
- `src/pm_alpha/historical.py`: CSV and Kalshi candlestick ingestion.
- `src/pm_alpha/report.py`: per-market SQLite diagnostics.

## Current live architecture

The Oracle VM runs one systemd service. The service:

1. Calls `KalshiMarketDiscovery` when `--discover` is enabled.
2. Selects ordinary binary markets and excludes `KXMV` multivariate markets.
3. Polls each ticker's public order book.
4. Normalizes YES bids and synthetic YES asks.
5. Writes snapshots to SQLite.
6. Sends each snapshot through four strategies.
7. Simulates paper fills against observed top-of-book liquidity.
8. Persists paper orders, fills, positions, and equity.

The process explicitly logs:

```text
PAPER MODE ONLY: intents are simulated against snapshots; no live orders are sent
```

There is no authenticated Kalshi client, no private-key loading, and no order
placement endpoint in the current system.

## Kalshi order-book normalization

Kalshi returns bids for YES and NO rather than explicit asks:

```text
YES bid = best YES bid
YES ask = 1 - best NO bid
```

The current API response uses:

```json
{
  "orderbook_fp": {
    "yes_dollars": [{"price_dollars": "0.40", "quantity": "3"}],
    "no_dollars": [{"price_dollars": "0.55", "quantity": "4"}]
  }
}
```

The adapter also supports the older shape:

```json
{
  "orderbook": {
    "yes": [[40, 3]],
    "no": [[55, 4]]
  }
}
```

The parser fix was important. The first overnight database contained
approximately 5.28 million rows but zero usable quotes because the old parser
ignored `orderbook_fp`.

The source now:

- Limits concurrent requests to two.
- Sends a user agent.
- Retries 429 and common 5xx responses with bounded backoff.
- Logs and skips an unavailable ticker rather than killing the process.

## Oracle VM

Known VM connection details:

```text
User: ubuntu
Host: 157.151.132.129
SSH key on Windows:
C:\Users\Will Palaia\Downloads\oracle cloud\Prediction Market Alpha Generation Ideas.key
```

Do not commit, upload, or paste the private key. The path above is local
metadata only.

The VM data/service paths are:

```text
Repository: /opt/pm-alpha
Virtualenv: /opt/pm-alpha/.venv
SQLite: /var/lib/pm-alpha/market_data.sqlite
Systemd unit: /etc/systemd/system/pm-alpha-paper.service
Service user: pmalpha
```

Recommended VM shape:

- Ubuntu 24.04 LTS.
- Ampere A1 Flex, 1 OCPU/6 GB RAM, or a small AMD instance.
- 50 GB disk.
- SSH ingress only from the operator's IP.

The service survives local computer shutdown and SSH disconnection because it
runs on the VM under systemd.

### Useful SSH commands

Check service:

```powershell
$key = "C:\Users\Will Palaia\Downloads\oracle cloud\Prediction Market Alpha Generation Ideas.key"
ssh -i $key ubuntu@157.151.132.129 "sudo systemctl is-active pm-alpha-paper"
```

Check restart status/logs:

```powershell
ssh -i $key ubuntu@157.151.132.129 `
  "sudo systemctl status pm-alpha-paper --no-pager -l; sudo journalctl -u pm-alpha-paper --since '10 minutes ago' --no-pager"
```

Check paper tables:

```powershell
ssh -i $key ubuntu@157.151.132.129 @"
python3 - <<'PY'
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
"@
```

Update the VM from public GitHub:

```bash
cd /opt/pm-alpha
sudo git pull --ff-only
sudo /opt/pm-alpha/.venv/bin/pip install -e /opt/pm-alpha
sudo systemctl restart pm-alpha-paper
sudo systemctl is-active pm-alpha-paper
```

The currently running VM service was manually configured with dynamic
discovery and a 10-second interval. The deployed unit command includes:

```text
--db /var/lib/pm-alpha/market_data.sqlite --interval 10 --discover
```

The checked-in service file is still a generic template; keep the VM's
production unit configuration in mind when editing deployment automation.

## One-command database sync from Windows

The database does not need to be copied for forward paper trading. Copying is
only for retrospective analysis, backups, and debugging. Runtime state is
separate from Git because it is mutable, potentially multi-gigabyte, and must
not contain credentials.

From the repository root:

```powershell
.\deploy\pull_oracle_data.ps1 `
  -SshKey "C:\Users\Will Palaia\Downloads\oracle cloud\Prediction Market Alpha Generation Ideas.key" `
  -OracleHost "157.151.132.129" `
  -Output "data\oracle_2026-09-25.sqlite" `
  -RunAnalysis
```

The helper:

1. Stops the systemd service briefly.
2. Copies `/var/lib/pm-alpha/market_data.sqlite` to a temporary readable file.
3. Restarts the service.
4. Downloads the temporary file with `scp`.
5. Removes the temporary remote copy.
6. Optionally runs `inspect_market_data.py` and `run_sweep.py`.

WSL is optional. Native Windows OpenSSH is sufficient.

## SQLite schema

The main snapshot table is `market_snapshots`:

- `timestamp_ms`
- `venue`
- `market_id`
- `yes_bid`
- `yes_ask`
- `bid_size`
- `ask_size`
- `resolved`
- `settlement_yes`

Paper tables:

- `paper_orders`: every simulated intent, status, fill quantity, and rejection.
- `paper_fills`: simulated executions, side, price, quantity, and fee.
- `paper_positions`: current quantity, average cost, realized P&L, settlement state.
- `paper_equity`: cash, marked position value, equity, and cumulative fees.

SQLite uses a 30-second busy timeout and WAL mode. Snapshot batches are
written with `append_many` to reduce lock contention.

## Strategies implemented

### Threshold buying

`BuyBelowThreshold` buys YES when the ask is below a configured threshold.
The sweep uses thresholds 0.10, 0.25, 0.50, 0.75, and 0.90.

Risk: an arbitrary threshold is not a probability model. A low-priced contract
can still be overpriced, and rare losses can dominate many small wins.

### Momentum

`MomentumStrategy` buys after the ask rises by a minimum amount relative to
recent history. The sweep uses minimum moves 0.02, 0.05, and 0.10.

Risk: the move may already be fully incorporated; spread, latency, and
reversal risk can erase the signal.

### Mean reversion

`MeanReversionStrategy` buys when the ask is below a lagged rolling mean by a
configured deviation. The sweep uses deviations 0.03, 0.05, and 0.10.

Risk: a true information event can cause a permanent repricing, so buying a
falling market can accumulate losses.

### Stable high probability / “boring 70-cent” proxy

`StableHighProbabilityStrategy` buys once after several observations remain in
a narrow band, defaulting to 0.68–0.72 with a five-observation lookback and a
0.03 range limit. The sweep includes bands 0.65–0.75, 0.68–0.72, and 0.70–0.80.

This is only a price-stability proxy. The snapshot schema does not currently
include market expiry or full event metadata, so it is not yet a valid test of
“one month away.” A contract priced near 0.70 still implies roughly a 30%
binary loss probability before fees if the price is calibrated, and one loss
can erase many small wins.

### Complementary YES/NO arbitrage

`find_complement_opportunity` checks whether buying YES and NO together costs
less than the guaranteed $1 payout after fees. `paired.py` stress-tests
non-atomic leg execution.

Risk: one leg can fill while the other fails, books can move, fees can erase a
small edge, and opportunities may be rare.

### External-reference market making

`market_making.py` accepts timestamped fair probabilities from an authorized
external source. It quotes around the fair reference and fills only when the
observed Kalshi book crosses the quote.

Required reference CSV:

```csv
timestamp_ms,market_id,fair_probability,source
1760000000000,KX-MARKET-TICKER,0.57,authorized_feed
```

Risk: false contract equivalence, sportsbook vig, stale references, queue
position, adverse selection, inventory accumulation, and cancellation latency.
The current simulator does not prove that a displayed spread is executable.

### Dynamic market discovery

`KalshiMarketDiscovery` is infrastructure, not alpha. It finds ordinary binary
markets and excludes multivariate markets. Low liquidity may indicate
inactivity rather than mispricing and may make fills impossible.

## What has been backtested

Automated validation currently has 27 passing tests. Tests cover:

- Event-driven replay.
- Latency and same-snapshot fill rules.
- Partial fills and liquidity consumption.
- Fees and cash constraints.
- Settlement and unresolved inventory.
- Paired-leg execution stress.
- Chronological walk-forward windows.
- Historical CSV/candlestick parsing.
- Kalshi old and current order-book schemas.
- SQLite storage and diagnostics.
- Strategy baselines and sweeps.
- Market-making fills, fees, inventory, and adverse-selection inputs.
- Persistent paper portfolio fills, positions, equity, and multi-strategy fanout.

Code compilation and `git diff --check` have also passed during the latest
implementation cycles.

What has *not* been proven:

- No strategy has been proven profitable with real money.
- No strategy has enough settled forward markets for statistical confidence.
- No full-depth WebSocket or queue-position capture exists.
- No authorized sportsbook reference feed is integrated.
- No calibrated probability model or Brier/log-loss analysis exists.
- No robust bootstrap confidence intervals or multiple-testing correction exists.
- No live order adapter exists.

The first copied overnight database was not usable for strategy evaluation:
it had approximately 5.28 million snapshots but zero quotes because of the
old API parser. After the parser and operational fixes, the VM began producing
quoted rows. A later verification showed the service active with zero restarts,
5,296,231 total rows, 673 quoted rows, 43 quoted markets, and 36 observations
in the 0.68–0.72 band. That was still only an initial sample, not a result.

After live paper trading was deployed, the VM was verified active with five
paper orders and five simulated fills. A sample account had equity around
$99.12 from a $100 starting balance after fees. This was far too early to
interpret.

## How paper success is judged currently

The current engine records:

- Ending cash.
- Marked position value.
- Equity.
- Cumulative fees.
- Orders and rejection reasons.
- Fills and quantities.
- Average cost and realized P&L.
- Current inventory.
- Settlement payouts when a resolved snapshot is received.

The offline sweep records ending equity, return fraction, fees, filled quantity,
rejected orders, and unresolved inventory.

This is not enough for promotion. The next analysis layer should add:

- Maximum drawdown and recovery time.
- Daily/weekly return volatility.
- Sharpe-like and downside-risk measures.
- Per-market and per-strategy attribution.
- Fill rate and quote coverage.
- Exposure and concentration limits.
- Bootstrap confidence intervals.
- Walk-forward train/test performance.
- Fee, latency, slippage, and fill-rate stress.
- Outcome calibration/Brier score for directional strategies.
- Negative controls and shuffled-outcome tests.
- Settlement-complete rather than mark-to-market-only results.

## Recommended next steps for a new agent

1. Let the Oracle paper trader run for several days without changing the
   database manually.
2. Use `pull_oracle_data.ps1 -RunAnalysis` to create a dated local copy.
3. Inspect quote coverage and settlement coverage before interpreting P&L.
4. Add a report for `paper_orders`, `paper_fills`, `paper_equity`, and
   strategy-level drawdown.
5. Add market metadata/expiry to snapshots so the one-month hypothesis can be
   tested directly.
6. Add explicit strategy names to paper order IDs or a strategy column if
   attribution becomes ambiguous.
7. Add full-depth or WebSocket capture before making market-making claims.
8. Add authorized external reference data and strict contract matching.
9. Run untouched chronological holdouts and fee/latency stress.
10. Keep all live execution disabled until the evidence gates are met.

## Safety and operating rules

- Never commit `.env`, private keys, API credentials, or the SQLite runtime DB.
- Do not treat mark-to-market gains as realized profit.
- Do not assume an apparent contract match is economically identical.
- Do not increase paper capital or enable live orders based on a short sample.
- Do not delete or overwrite the Oracle database without a backup.
- Keep the Oracle service public-data-only and paper-only.
- If changing the systemd unit, verify `systemctl is-active` and inspect recent
  journal output before ending the task.

