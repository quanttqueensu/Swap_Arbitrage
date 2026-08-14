# Swap Arbitrage technical documentation

## Quick start

### Purpose and boundary

The project researches a maturity-matched swap-spread arbitrage hypothesis,
expresses it as pure strategy records, and evaluates it with historical research
scripts and a causal synthetic replay engine. It also contains paper-data and
paper-order adapters for IBKR.

**The repository is permanently paper-only. Real-money trading is not a
supported mode.** “Live data” means currently observed data; it
does not authorize live-capital execution. Development and test commands must
not connect to IBKR or public data sources. A human operator alone may start an
explicitly documented paper session after completing the paper-run safety
checklist.

The causal replay engine proves deterministic mechanics, not historical alpha
or executable costs. The `complete_2y_5y` label is reserved for a validated
canonical strategy/data integration. Synthetic replay uses `synthetic_fixture`
and `synthetic_mechanics_only`.

### Environment setup

Python 3.12 is the supported interpreter. Runtime dependencies are pinned in
[`requirements.txt`](../requirements.txt): NumPy 2.3.5, pandas 3.0.1, and
ib_insync 0.9.86.

From PowerShell in the repository root, create a fresh environment:

```powershell
& "C:\Path\To\Python312\python.exe" -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip check
```

Package installation may contact the configured Python package index. The
`.venv` path is ignored and is never portable; recreate it if its launcher
names a missing interpreter.

Workspace verification used bundled Python 3.12.13 with existing ignored
site-packages because the local environment launcher is broken. The loaded
NumPy 2.5.0 and pandas 3.0.3 differ from the declared pins, although ib_insync
0.9.86 and `pip check` pass. Treat those results as workspace compatibility
evidence, not fresh-install reproducibility.

Never commit an account ID, credential, token, endpoint secret, `.env` content,
or broker object. Set secrets only in the operator's environment. Examples use
placeholders.

### Offline verification commands

After activating a valid environment, these commands are supported and make no
external or broker connection:

```powershell
python -m unittest docs.tests.test_naive_backtest -v
python -m unittest docs.tests.test_schema_contracts -v
python -m unittest discover -s docs/tests -v
python -m unittest discover -s agents/agent_0/tests -v
python -m compileall -q backtesting strategy data_pipeline agents/agent_0 docs/tests
python signal_pipeline.py --self-check
python risk_pipeline.py --self-check
python backtest_engine.py --self-check
```

The full suite imports `ib_insync` to prove the pinned class is available, but
tests replace broker behavior with fakes and socket guards. Importing the class
does not connect to IBKR.

The backtesting package is library-first and has no command-line runner. Its
executable example is
`docs/tests/test_naive_backtest.py`, which calls `run_backtest` and
`write_results` with synthetic `ReplayEvent` values. Do not invent a historical
run command until a canonical-data adapter exists.

### Architecture at a glance

```text
public/captured sources
        |
        v
data_pipeline/historical_data -> canonical CSV partitions -> schema validation
                                                       |
                          no CSV -> MarketSnapshot adapter

strategy/*.py (pure records, equations, signals, sizing, risk, portfolio)
        |
        +--------------------------+
        |                          |
        v                          v
backtesting/*.py              shared paper strategy adapter not implemented
synthetic causal replay
        |
        v
validated backtest CSVs

IBKR paper session -> IbkrPaperRecorder -> validated paper CSVs
IBKR paper session <- Agent 0 random policy/manual operator

legacy research path:
historical_data_builder -> signal_pipeline -> risk_pipeline -> backtest_engine
```

The recorder and Agent 0 are separate consumers. The recorder captures and
validates paper events but cannot connect, submit, or cancel. Agent 0 can submit
paper orders when a human starts it; it is a random experiment and does not use
the swap-arbitrage signal or current positions.

## System reference

### Directory and component ownership

| Path | Responsibility | Must not do |
| --- | --- | --- |
| `strategy/models.py` | Immutable shared records and enums | Files, network, broker calls |
| `strategy/spread.py` | Spread, cost-buffer, and hedge equations | Rolling state or P&L accounting |
| `strategy/signal_generation.py` | Causal rolling features and state transitions | Create orders |
| `strategy/position_sizing.py` | DV01 target scaling and integer hedge selection | Call a broker |
| `strategy/risk_signals.py` | Allow, scale, block, or flatten with reason codes | Submit orders |
| `strategy/costs.py` | Naive and observed directional cost calculations | Choose signals |
| `strategy/portfolio.py` | Rank-order portfolio composition and DV01 limits | Re-rank inputs |
| `data_pipeline/contracts.py` | Versioned schema catalog and CSV validation | Acquire data or decide trades |
| `data_pipeline/historical_data/` | Source acquisition and canonical transformations | Decide trades |
| `data_pipeline/live_data_pipeline/` | Injected IBKR paper recorder and atomic event store | Create connections or place/cancel orders |
| `backtesting/` | Replay, fills, accounting, and report mapping | Copy strategy equations |
| `agents/agent_0/` | Random weekly paper-order experiment | Claim complete-strategy behavior or use live accounts |
| root `*_pipeline.py`, `backtest_engine.py` | Maintained legacy pandas research path | Serve as the canonical backtest adapter |
| `docs/tests/` | Offline contracts, regression tests, and golden examples | Contact external systems |
| `docs/verification/` | Historical verification evidence | Override source or contract behavior |

The aspirational `agents/shared/` boundary is not implemented. Add it only when
a real second consumer requires it.

### Canonical data and provenance

The durable canonical layout is:

- `data/rates/rates_YYYY.csv`: rate observations in basis points;
- `data/futures/futures_settlements_YYYY.csv`: futures settlements;
- `data/contract_risk/contracts.csv`: effective-dated contract reference;
- `data/contract_risk/contract_risk_YYYY.csv`: DV01 and rate-sensitivity sign;
- `data/market/daily_market_YYYY.csv`: long-form daily market observations;
- `data/paper/agent_N/run_id/*.csv`: paper quotes, orders, fills, positions;
- `data/results/backtests/run_id/*.csv`: immutable backtest results when written.

`historical_data_builder.py` contains network-capable source acquisition for
configured US Treasury, New York Fed, Eris, and Treasury-futures sources.
`canonicalize.py` converts captured source files into the approved long-form
records. Acquisition and canonicalization are separate: tests exercise
transformations from fixtures without refreshing a public source.

Every canonical writer follows the invariants in
[Project contracts: data validation](master-plan/PROJECT_CONTRACTS.md#data-validation-invariants):
exact headers, explicit units, unique keys, causal timestamps, deterministic
ordering, validated temporary output, and atomic replacement. The executable
schema version is `1.0.0` in `data_pipeline/contracts.py`.

The canonical partitions are not sufficient to run the complete strategy.
Specifically, no maintained adapter joins them into the
shared `MarketSnapshot` records required by `backtesting.run_backtest`.

### Shared strategy flow

The pure boundary uses the immutable records described in
[Project contracts: shared interfaces](master-plan/PROJECT_CONTRACTS.md#shared-strategy-interfaces):

1. `MarketSnapshot` supplies one causal view of rates, instruments, contract
   metadata, observation times, paper positions, and working orders.
2. `strategy.spread` produces maturity-matched spread and cost observations.
3. `strategy.signal_generation` emits a `SignalDecision` using only observations
   available at the decision timestamp.
4. `strategy.position_sizing` converts an eligible direction into signed integer
   swap/Treasury quantities under liquidity, contract, and gross-DV01 limits.
5. `strategy.portfolio` consumes the already approved rank order and admits
   targets without exceeding portfolio gross/net DV01.
6. `strategy.risk_signals` produces an explicit `RiskDecision`; it never routes
   an order.
7. An adapter turns approved `OrderIntent` records into simulated fills or paper
   orders. Strategy code does not import either adapter.

Invalid values return no target/decision or a blocking reason. Missing or stale
market data, invalid bid/ask, nonpositive price/DV01, capacity, loss, drawdown,
margin, disconnection, reconciliation, and roll failures are intended to block
new risk as defined by the authoritative contract.

### Backtest flow and outputs

`backtesting.run_backtest` accepts ordered `ReplayEvent` values and a pure
strategy callable. For each event it:

1. marks positions held from the previous event;
2. charges elapsed-day financing;
3. processes only previously queued orders;
4. applies fill capacity, partial fills, rejection, and expiry;
5. calls the strategy with current positions and working orders;
6. queues new intents for a later event; and
7. records daily accounting and end-of-event positions.

A decision therefore cannot fill on its own event. P&L belongs to the quantity
held before the price change. Direct reversal closes the old lifecycle and opens
the new one only for filled opening quantity. Per-instrument ownership keeps old
and new trade P&L separate during partial reversals.

`write_results(result, output_root)` creates one run directory containing:

- `manifest.csv`;
- `daily.csv`;
- `decisions.csv`;
- `orders.csv`;
- `fills.csv`;
- `trades.csv`;
- `positions.csv`;
- `summary.csv`.

Each non-manifest file maps to an approved `backtest_*` schema and validates
before replacement. The manifest records schema/configuration versions,
coverage, row counts, input and artifact hashes, assumptions, and exact missing
input locations. The configuration hash covers the declared configuration
version plus effective mode, scope, window, and cost assumptions.

A missing held input retains available-leg partial accounting and records each
`date:instrument_id:field`. Any run with such a location is diagnostic mechanics
evidence, not complete economic evidence. The fixed assumptions and reconciled
example are covered by `docs/tests/test_naive_backtest.py`.

### Paper-data lifecycle

`IbkrPaperRecorder` receives an already connected, injected broker object. It
validates exact local paper settings, connection state, and managed account
before requesting quotes or recording events. Its public responsibilities are
session validation, quote requests, and quote/order/fill/position recording.
It has no connect, place, or cancel method.

`PaperEventStore` writes only approved paper schemas beneath
`data/paper/agent_N/run_id/`. It rejects unsafe path identifiers, account-like
values, credentials/endpoints, duplicate conflicts, invalid order evolution,
bad types, and noncanonical ordering. It validates a temporary sibling and
atomically replaces the destination.

Broker-derived objects are untrusted. Normalization errors cross the boundary
as generic `PaperSafetyError` messages so credentials, endpoints, client IDs,
and account data are not retained in exception chains.

### Agent 0 paper execution lifecycle

Agent 0 is a deliberately separate random paper experiment:

1. `RandomPolicy` builds 25 orders for the next Monday-Friday window, five per
   day, using positive configured caps and a deterministic seed when supplied.
2. The local upcoming ledger is reconciled before connection.
3. `broker.connect` enforces paper-only mode, loopback host, port 7497, client ID
   30, a `DU` account prefix, and visibility in `managedAccounts()`.
4. Contract selection qualifies the nearest eligible expiries.
5. Existing open orders are reconciled; the remaining week must fit the
   per-contract/side working-order cap before submission starts.
6. Every planned order receives a what-if margin preview. Quantity is reduced
   until at least the configured 10% reserve remains; if quantity one fails,
   submission stops.
7. Accepted `MKT`/`DAY` orders use `GoodAfterTime`; deterministic order
   references support restart reconciliation.
8. Local upcoming/previous CSVs update only through the Agent 0 runner.

Agent 0 does not use swap-arbitrage signals, current positions, automatic
flattening, or live accounts. Its missing-sizing fallback and contract tie
behavior are known limitations and must not be inherited implicitly.

The operator-only entry points are documented in
[`agents/agent_0/SETTINGS.md`](../agents/agent_0/SETTINGS.md). They can submit or
globally cancel paper orders and are not part of setup or test verification.
Never place an account literal in a command saved to documentation; use a local
environment variable and review the permanent
[paper-agent safety contract](master-plan/PROJECT_CONTRACTS.md#paper-agent-contracts)
before a human starts the process.

That linked settings guide retains one known concrete paper-account value. Do
not copy or extend it. This onboarding guide uses placeholders, and account
values belong only in the operator's environment.

### Testing and failure handling

The main offline suites are `docs/tests` and `agents/agent_0/tests`. Important
focused modules include:

- `test_strategy_equation_examples`: approved numerical/sign fixtures;
- `test_schema_contracts`: headers, types, keys, ordering, and semantic rules;
- `test_ibkr_paper_recorder`: paper session, privacy, network, and store guards;
- `test_naive_backtest`: timing, accounting, reversals, reports, and manifests;
- `test_characterization`: Agent 0 routing, reconciliation, margin, and ledger
  behavior.

Failures should be handled at the owning boundary:

- schema/serialization error: do not replace the destination;
- missing or stale strategy input: return no target or block with reasons;
- unsafe paper configuration: fail before broker work;
- broker mismatch/disconnection: stop routing and disconnect in `finally`;
- missing replay held data: retain the diagnostic partial row
  and inspect `missing_input_locations`;
- missing realistic cost data: block or use only an approved, recorded
  conservative fallback—never silently use zero.

## Technical reference

### Equations, units, signs, and timing

The complete definitions and frozen parameter choices live in
[Project contracts: economic equations](master-plan/PROJECT_CONTRACTS.md#economic-hypothesis-equations)
and [Strategy equations](research/strategy-equations.md). This guide retains
only the conventions needed to read code and artifacts:

- rates/spreads: basis points (`_bps`);
- raw rate decimals: `_decimal`, converted once at the input boundary;
- prices: quoted price points (`_price`);
- DV01: USD per one-basis-point rate increase (`_dv01_usd_per_bp`);
- P&L/costs: USD (`_usd`);
- event times: timezone-aware UTC ISO 8601 (`_utc`); daily research uses ISO
  dates;
- signed contract quantity: positive is long the exchange contract, negative
  is short.

For prior quantity `q`, price multiplier `M`, and same-contract price change
`ΔP`, leg P&L before costs is `q × M × ΔP`. Basket P&L sums the legs and then
subtracts transaction, financing, and roll costs. Cross-contract price changes
are never treated as same-contract P&L.

One long contract has signed rate exposure `δ = sensitivity_sign × |DV01|`.
Portfolio net DV01 sums `q × δ`; gross DV01 sums `|q × δ|`. Integer hedge
selection minimizes residual net DV01 inside the approved contract, liquidity,
gross-DV01, and margin limits. Economic direction and exchange side are
separate and must stay covered by golden sign tests.

Features at decision time `t` may use only information observable by `t`.
Positions held from `t-1` earn the move to `t`; orders fill no earlier than
their declared eligibility. A direct reversal is exit plus entry, so both
turnovers and both costs apply. Rolls charge close and open.

### Configuration and dependencies

| Item | Requirement | Authority |
| --- | --- | --- |
| Python | 3.12 | `README.md` |
| NumPy | 2.3.5 | `requirements.txt` |
| pandas | 3.0.1 | `requirements.txt` |
| ib_insync | 0.9.86 | `requirements.txt` |
| Canonical schema | 1.0.0 | `data_pipeline/contracts.py` |
| Strategy specification | `p10.strategy-equations.v1` | `strategy/spread.py` |
| Sizing/risk version | `p33.position-sizing-risk.v1` | `strategy/position_sizing.py` |
| Agent 0 route | loopback, TWS paper port 7497, client 30, `DU` account | `agents/agent_0/config.py` |

The root `config.py` owns legacy research paths, source parameters, maturity
maps, risk constants, and the loopback broker host. Agent 0 imports selected
values through an isolated module load, then adds its immutable paper guards.
Do not duplicate account IDs or secrets in configuration.

### External API/package facts relied upon

Verified 2026-08-09:

- IBKR's current TWS API documentation says socket clients must be enabled and
  the configured socket port must match the client; default TWS ports are 7496
  live and 7497 paper. The project additionally fixes loopback, 7497, client 30,
  `DU` account policy, and managed-account validation. Source:
  [IBKR TWS API documentation](https://ibkrcampus.com/campus/ibkr-api-page/twsapi-doc/).
- Agent 0 relies on TWS/IB Gateway being open and authenticated before its local
  socket connection. It uses `reqAllOpenOrders` for cross-client snapshots,
  `whatIfOrder` for margin preview, and `placeOrder` only after local guards.
  Source: the same IBKR documentation and
  [TWS API reference](https://ibkrcampus.com/campus/ibkr-api-page/twsapi-ref/).
- `ib_insync` 0.9.86 is the pinned third-party adapter. Its `IB` surface supplies
  `connect`, `qualifyContracts`, `reqAllOpenOrders`, `whatIfOrder`, and
  `placeOrder`. The upstream repository was archived on 2024-03-14 and is
  read-only, so a future dependency change requires an explicit compatibility
  review rather than an unrecorded upgrade. Source:
  [ib_insync upstream repository](https://github.com/erdewit/ib_insync).

IBKR states that it does not support third-party package implementations. The
project therefore treats its own pinned-version tests and paper guards as the
runtime contract; vendor documentation remains authoritative for the socket
API and TWS configuration.

### Troubleshooting

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `.venv` says its Python is missing | Environment was moved or its base interpreter was removed | Delete/recreate only the intended `.venv`; install from `requirements.txt` |
| `ModuleNotFoundError: ib_insync` | Dependencies are not installed in the active interpreter | Activate the correct environment and install the pinned requirements |
| `eventkit` warns that no event loop exists during tests | Known import-time deprecation warning in the pinned stack | Treat as a warning unless a test fails; do not suppress globally |
| No complete historical backtest command exists | Canonical-to-`MarketSnapshot` adapter is not implemented | Use the synthetic replay test as mechanics evidence; add a command only with a validated adapter |
| Replay output has `missing_input_locations` | A held mark, multiplier, execution input, or contract record was absent | Treat the run as diagnostic; inspect every recorded location |
| Report writing raises schema validation | A row violates the approved catalog | Fix the owning adapter/record; do not widen or bypass the schema |
| Agent 0 refuses the account/port | Paper-only policy or managed-account visibility failed | Correct the paper TWS session; never weaken the guard |
| Agent 0 cannot connect | TWS/IB Gateway is closed, socket clients are disabled, port differs, or client ID conflicts | Check the paper session and TWS API settings; do not switch to a live port |
| What-if blocks quantity one | Margin reserve would be breached | Stop; do not bypass preview or reduce the reserve |
| Local accepted order is absent at IBKR | Broker snapshot is authoritative | Reconciliation resets it to planned according to Agent 0 behavior |
| New files under `docs/` do not appear in `git status` | The repository ignores the whole `docs` tree | Use explicit inspection or force-add the intended document; change ignore policy separately |
| `git diff --check` reports unrelated Agent 0 whitespace | Existing working-tree changes contain whitespace errors | Repair only in a change that owns those files |

### Known limitations

- Canonical maturity, contract, and liquidity coverage plus a canonical replay
  adapter are prerequisites for a complete historical strategy run.
- Time-varying cost, roll, funding, and liquidity inputs require validated
  source coverage and conservative missing-data rules.
- Agent 0's missing-sizing fallback and contract tie-break behavior are known
  limitations and must not be inherited implicitly.
- The local environment launcher is not portable, and a fresh pinned-environment
  verification is still required for clean-install reproducibility.
- The blanket `docs` ignore rule can hide new documentation from normal Git
  status; explicitly track intended documents.
- The linked Agent 0 settings guide retains a known account-literal exception;
  this guide contains no account literal.

### Glossary

| Term | Meaning |
| --- | --- |
| basis point (bp) | One hundredth of one percentage point |
| DV01 | USD change for a one-basis-point parallel rate increase |
| fixed swap spread | Maturity-matched swap rate minus Treasury yield |
| funding spread | Approved floating reference minus consistent repo rate |
| gross excess spread | Fixed swap spread minus expected funding burden |
| net opportunity | Directional gross excess less round-trip cost buffer |
| traditional direction | Receive fixed / short Treasury economic direction (`+1`) |
| reverse direction | Opposite economic direction (`-1`) |
| decision timestamp | Earliest instant when every feature used is observable |
| `MarketSnapshot` | Immutable causal strategy input record |
| `OrderIntent` | Broker-independent, paper-only execution request |
| canonical data | Versioned, validated long-form records under the approved schemas |
| synthetic mechanics | Deterministic replay evidence that does not establish alpha or executable costs |
| Agent 0 | Random weekly IBKR paper-order experiment, not the complete strategy |
| paper recorder | Injected read/record adapter that cannot connect, submit, or cancel |
| lifecycle trade | Maturity/direction record from filled opening exposure through closure |
| fail closed | Refuse new risk or output replacement when required validation fails |

## Maintaining this document

Update this guide in the same change whenever architecture, interfaces,
dependencies, external API assumptions, equations, schemas, supported commands,
outputs, or operating procedures change. Link to the new authoritative version;
do not rewrite historical meaning in place. Re-run every command still described
as supported and record exceptions in the relevant verification file.
