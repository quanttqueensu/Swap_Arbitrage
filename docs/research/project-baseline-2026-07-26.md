# Swap Arbitrage Repository Baseline

**Prompt:** P00 — Record a reproducible repository baseline

**Required filename date:** 2026-07-26

**Evidence observed:** 2026-07-28, America/Toronto

**Repository:** `C:\Users\jaydo_0v7vk2o\Downloads\Swap_Arbitrage`

**Scope:** Local, read-only inspection of the repository, ignored local
artifacts, and existing CSV/cache data. P00 created this report and its
verification record only. It did not repair the environment, change strategy
or Agent 0 behavior, rewrite data, contact Cloudflare or IBKR, or submit/cancel
an order.

## Executive facts

1. The checkout is on `main` at commit `1d7e75b` and was already dirty before
   P00. The pre-existing changes are enumerated below and must be preserved.
2. The project `.venv` cannot start because its base Windows Store Python no
   longer exists. `python` is also absent from `PATH`.
3. There is no dependency manifest or supported-Python declaration. Direct
   third-party imports are NumPy, pandas, `ib_insync`, boto3, botocore, and
   python-dotenv; the broken `.venv` contains only the first three application
   dependencies and their transitive packages.
4. The only automated test file contains 28 data/risk/backtest tests. There are
   no Agent 0 tests and therefore no automated fake-broker proof of paper-only
   routing.
5. A separate bundled Python 3.12.13 can run the current 28 tests and all four
   self-checks, but it is not the project's declared or reproducible runtime.
6. Agent 0 code generates 5 orders on each of five weekdays: 25 orders/week.
   `SETTINGS.md` incorrectly calls that 50. Older design/plan documents specify
   100 or 250, so no authoritative weekly count exists.
7. Agent 0 contains static paper safeguards—port 7497, `DU` account prefix,
   `PAPER_ONLY=True`, and `LIVE_TRADING_ENABLED=False`—but its normal command
   connects and transmits orders; there is no default dry-run.
8. Current signal decisions are Eris-price/Treasury-futures-price residual
   proxies. They do not implement `CMS - CMT`, expected repo funding, net
   excess spread, or the complete economic hypothesis.
9. Current backtests use prior-row contract quantities for current-row P&L,
   but have no explicit order/fill engine. Both transaction-cost defaults are
   zero.
10. The 12 top-level research CSVs contain 4 to 99 columns. Their candidate
    keys have no duplicates, but the 1,474 raw Eris cache files use two header
    orderings and 20 files contain repeated `EvaluationDate + Symbol` values.
11. Local R2 inventory exists, but the repository has no selective Quantt
    ingestion or canonical lineage. Existing Agent 0 code can resolve and
    submit IBKR paper futures orders, but cannot record canonical quotes,
    fills, or positions.
12. The pre-existing `.gitignore` modification ignores all of `docs`, so this
    required report and its verification record are locally present but
    ignored unless that user-owned rule is later changed or the files are
    explicitly force-added.

## 1. Git and preserved worktree state

### Branch and recent commits

- Branch: `main`
- HEAD: `1d7e75b` — `docs: add swap arbitrage master plan`

Recent commits:

| Commit | Date | Subject |
|---|---|---|
| `1d7e75b` | 2026-07-26 | docs: add swap arbitrage master plan |
| `6b1c41e` | 2026-07-25 | feat: add standalone Cloudflare R2 connection test |
| `5acc6a8` | 2026-07-25 | docs: plan Cloudflare R2 object listing |
| `104ba8b` | 2026-07-25 | docs: design Cloudflare R2 object listing |
| `5f592c1` | 2026-07-19 | feat: add Treasury futures risk master |
| `7374467` | 2026-07-19 | chore: enforce canonical DV01 data flow |
| `7567e66` | 2026-07-19 | refactor: backtest from contract quantities |
| `8a11714` | 2026-07-19 | refactor: size risk from CME master |
| `1e148be` | 2026-07-19 | feat: build canonical CME swap master |
| `d7e836f` | 2026-07-19 | docs: plan CME DV01 master implementation |

### Changes present before P00

| Path | Git state | Baseline interpretation |
|---|---|---|
| `.gitignore` | Modified, unstaged | User-owned change. It adds/changes ignore rules including `docs`, `.env`, `r2_objects.csv`, caches, generated data, and Agent order CSVs. Preserve unchanged. |
| `cloudflare_r2_test.py` | Deleted, staged | User-owned staged deletion of the committed standalone R2 test. Preserve deletion and index state. |
| `docs/master-plan/MASTER_PLAN.md` | Modified, unstaged | User-approved Phase 1 planning amendments made immediately before P00. Preserve. |
| `docs/master-plan/PROJECT_CONTRACTS.md` | Modified, unstaged | Same approved amendment set. Preserve. |
| `docs/master-plan/PROMPT_PLAYBOOK.md` | Modified, unstaged | Same approved amendment set. Preserve. |
| `docs/master-plan/VERIFICATION_GATES.md` | Modified, unstaged | Same approved amendment set. Preserve. |
| `r2_database_names.py` | Untracked | User-owned R2 object-inventory script. Preserve. |

P00 did not stage, unstage, restore, delete, or rewrite any of these paths.

### Relevant ignored local artifacts

`git check-ignore -v` maps:

| Artifact | Rule | Observed local count |
|---|---|---:|
| `.venv/` | `.gitignore:1` | 6,118 ignored files |
| `data/cache/` | `.gitignore:10` | 1,491 ignored files |
| `agents/agent_0/orders/*.csv` | `.gitignore:13` | 2 ignored files |
| `.env` | `.gitignore:15` | 1 ignored file |
| `r2_objects.csv` | `.gitignore:16` | 1 ignored file |
| `data/*.csv` | `.gitignore:9` | 12 ignored files |
| `docs` | `.gitignore:14` | 3 pre-existing untracked documents plus new P00 artifacts |

The three pre-existing ignored documents are the QUANTT hypothesis and the
Agent 0 weekly-orders design and implementation plan. The tracked
`docs/master-plan/` files remain tracked despite the newer broad ignore rule.

## 2. Runtime, dependencies, entry points, tests, and documents

### Python state

Project command:

```powershell
.\.venv\Scripts\python.exe --version
```

Result: exit 103.

```text
No Python at
"C:\Users\jaydo_0v7vk2o\AppData\Local\Microsoft\WindowsApps\
PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0\python.exe"
```

The `.venv\pyvenv.cfg` records CPython 3.12.10 at that missing path.
`python --version` also fails because `python` is not found on `PATH`.

A Codex-bundled interpreter exists at:

```text
C:\Users\jaydo_0v7vk2o\.cache\codex-runtimes\
codex-primary-runtime\dependencies\python\python.exe
```

It reports Python 3.12.13 and can run the current suite. This is supplemental
evidence only; P01 must establish the repository-supported runtime and clean
installation command.

### Dependency state

No `requirements*.txt`, `pyproject.toml`, lock file, `setup.py`, `setup.cfg`,
environment YAML, or `.python-version` exists.

Direct third-party imports:

| Import | Consumers | Broken `.venv` evidence |
|---|---|---|
| `numpy` | `signal_data.py`, `risk_data.py`, `backtest.py` | NumPy 2.5.0 metadata present |
| `pandas` | data, signal, risk, backtest, sizing, tests | pandas 3.0.3 metadata present |
| `ib_insync` | Agent 0 broker/contracts/orders | 0.9.86 metadata present |
| `boto3` | untracked `r2_database_names.py` | Not present |
| `botocore` | untracked `r2_database_names.py` | Not present |
| `dotenv` / python-dotenv | untracked `r2_database_names.py` | Not present |

The environment contains other transitive packages, including eventkit,
nest-asyncio, python-dateutil, six, and tzdata. Import discovery alone is not a
complete dependency specification.

Only environment-variable names were inspected; values were not printed:

- `R2_ACCESS_KEY_ID`
- `R2_ACCOUNT_ID`
- `R2_BUCKET`
- `R2_ENDPOINT`
- `R2_SECRET_ACCESS_KEY`

Agent 0 additionally reads `AGENT0_IBKR_ACCOUNT` and `AGENT0_RANDOM_SEED`, but
they are not present in the inspected project `.env`.

### Python entry points

| Command | Current responsibility | External activity without refresh/normal-run flags |
|---|---|---|
| `python raw_price_data.py` | Build rates, Eris, Treasury-futures, and merged raw CSVs | May use existing files by default; refresh flags perform public network reads |
| `python signal_data.py` | Build proxy signal CSV | Uses local raw data unless refresh flags are passed |
| `python risk_data.py` | Build position sizing/risk CSV | Uses local signal and master files unless refresh flags are passed |
| `python backtest.py` | Run proxy portfolio backtest and save a result CSV | Local files only |
| `python agents/agent_0/run.py` | Connect to IBKR paper, construct and submit the next weekly plan | Connects and can transmit orders; no default dry-run |
| `python agents/agent_0/run.py --cancel-all` | Global IBKR cancellation and local upcoming-order reset | Connects and cancels every visible working order |
| `python r2_database_names.py` | List R2 object metadata to `r2_objects.csv` | Connects to Cloudflare R2; dependencies currently absent from `.venv` |

### Automated checks

The only test file is `tests/test_dv01_pipeline.py`, containing 28
`unittest` tests:

- 2 derived-CSV tests
- 1 signal-calendar test
- 4 CME swap-master tests
- 8 Treasury-master tests
- 7 risk-master tests
- 6 backtest-master tests

No test imports `agents.agent_0`; there are no fake-broker or paper-routing
tests.

Self-check entry points exist for:

```powershell
python raw_price_data.py --self-check
python signal_data.py --self-check
python risk_data.py --self-check
python backtest.py --self-check
```

Using the bundled Python 3.12.13 on 2026-07-28:

- `python -m unittest discover -s tests -v`: 28 passed, 0 failed.
- `raw_price_data.py --self-check`: passed.
- `signal_data.py --self-check`: passed.
- `risk_data.py --self-check`: passed.
- `backtest.py --self-check`: passed.

These results do not satisfy MG1 because the project environment is broken and
cannot be recreated from a manifest.

### Documentation inventory

Tracked governing documents:

- `docs/master-plan/MASTER_PLAN.md`
- `docs/master-plan/PROJECT_CONTRACTS.md`
- `docs/master-plan/PROMPT_PLAYBOOK.md`
- `docs/master-plan/VERIFICATION_GATES.md`
- CME-DV01 and Cloudflare R2 design/implementation records under
  `docs/superpowers/`

Ignored, untracked documents:

- `docs/QUANTT Swap Arb Hypothesis 36e3a45c47d880409b95d6b9617f5c3b.md`
- `docs/superpowers/specs/2026-07-15-agent-0-weekly-orders-design.md`
- `docs/superpowers/plans/2026-07-15-agent-0-weekly-orders.md`

## 3. Agent 0 behavior, contradictions, and safeguards

### Weekly-order contradiction

`agents/agent_0/random_policy.py` selects the next Monday through Friday and
loops `ORDERS_PER_DAY` for each day. Current
`agents/agent_0/config.py` sets `ORDERS_PER_DAY = 5`. The implemented total is
therefore:

```text
5 weekdays × 5 orders/day = 25 orders/week
```

Evidence sources disagree:

| Source | Orders/day | Claimed or implied weekly total |
|---|---:|---:|
| Current code | 5 | 25 |
| Current `SETTINGS.md` | 5 | 50, arithmetically inconsistent |
| Ignored Agent 0 design | 20 | 100 |
| Early task in ignored implementation plan | 50 | 250 |
| Later task in the same ignored plan | 20 | 100 |
| Current master-plan audit | 5 | 50, repeats the settings error |
| Existing `upcoming.csv` | 5 on each of 5 dates | 25 accepted local records |

MG1 must record one authoritative count. P00 does not choose or change it.

### Instrument-universe contradiction

Current configuration allows:

- Eris: YIT (2Y), YIW (5Y)
- Treasury futures: ZT (2Y), ZF (5Y)

`SETTINGS.md` additionally lists ZN and ZB. Current Agent 0 code cannot select
those roots because they are absent from its imported configuration. The
settings document is stale or the code scope is incomplete; P00 makes no
behavior change.

### Current random policy

- Chooses uniformly among instruments with positive sizing caps.
- Chooses BUY or SELL independently at 50/50.
- Chooses activation time uniformly from 09:00 through 15:00
  `America/New_York`.
- Chooses an integer quantity from 1 through the selected instrument cap.
- Does not read swap-arbitrage signals or current positions.
- Has no flattening rule or position check.
- Uses an optional environment seed; without one, the plan is not
  reproducible.

### Paper-account safeguards present in code

- `PAPER_ONLY = True`
- `LIVE_TRADING_ENABLED = False`
- Hard-coded paper port 7497; any other configured port is rejected.
- Required account prefix `DU`.
- The configured account must appear in `ib.managedAccounts()`.
- `submit_order` rechecks paper settings and requires the order account to
  match the configured account.
- No production port 7496 or production enable flag was found.

### Safety and operational gaps

- None of the safeguards have automated Agent 0 tests.
- The normal command is mutating: it connects and calls `ib.placeOrder`.
- The `--account` CLI option overrides the environment variable, although the
  paper prefix and managed-account checks still apply.
- `--cancel-all` invokes `reqGlobalCancel()` for all orders visible to the
  connected session, including manual or other-client orders; this broad scope
  is documented.
- The code can query contract details, qualify contracts, inspect all open
  orders, run `whatIfOrder`, place orders, and globally cancel.
- It does not record market quotes, fills, executions, or broker positions in
  canonical schemas.
- `upcoming.csv` contains 25 locally accepted records for 2026-07-20 through
  2026-07-24 UTC; `previous.csv` is empty. P00 did not connect to IBKR to
  determine whether those local records match broker state.

## 4. Data inventory

### Top-level research and operational CSVs

Candidate keys are inferred from the current loaders and obvious identifiers;
they are not approved Phase 2 schemas.

| Path | Bytes | Rows | Columns | Date coverage | Candidate key | Duplicate key rows | Duplicate headers |
|---|---:|---:|---:|---|---|---:|---:|
| `data/cme_swap_data.csv` | 113,986 | 2,948 | 4 | 2020-09-04 to 2026-07-15 | `date,ticker` | 0 | 0 |
| `data/raw_price_data.csv` | 369,103 | 2,154 | 24 | 2018-01-02 to 2026-07-15 | `date` | 0 | 0 |
| `data/risk_data.csv` | 872,590 | 1,471 | 72 | 2020-09-04 to 2026-07-15 | `date` | 0 | 0 |
| `data/signal_data.csv` | 574,580 | 1,471 | 40 | 2020-09-04 to 2026-07-15 | `date` | 0 | 0 |
| `data/swap_rates.csv` | 125,622 | 1,474 | 5 | 2020-09-04 to 2026-07-15 | `date` | 0 | 0 |
| `data/treasury_futures.csv` | 116,115 | 1,471 | 5 | 2020-09-04 to 2026-07-15 | `date` | 0 | 0 |
| `data/treasury_futures_data.csv` | 118,306 | 2,942 | 4 | 2020-09-04 to 2026-07-15 | `date,ticker` | 0 | 0 |
| `data/treasury_rates.csv` | 177,175 | 2,143 | 16 | 2018-01-02 to 2026-07-15 | `date` | 0 | 0 |
| `data/swap_arb_backtest_2018-01-02_2026-07-07.csv` | 1,314,832 | 2,148 | 99 | 2018-01-02 to 2026-07-07 | `date` | 0 | 0 |
| `data/swap_arb_backtest_2020-09-04_2026-07-15.csv` | 1,209,338 | 1,471 | 99 | 2020-09-04 to 2026-07-15 | `date` | 0 | 0 |
| `data/swap_arb_backtest_2022-01-01_2024-12-31_now_2022_2024.csv` | 530,313 | 756 | 85 | 2022-01-03 to 2024-12-31 | `date` | 0 | 0 |
| `data/swap_arb_backtest_2022-01-01_2024-12-31_treasury_master.csv` | 645,206 | 753 | 99 | 2022-01-03 to 2024-12-31 | `date` | 0 | 0 |
| `r2_objects.csv` | 420,900 | 2,117 | 9 | modified 2026-06-26 to 2026-07-14 | `bucket,object_key` | 0 | 0 |
| `agents/agent_0/orders/upcoming.csv` | 2,103 | 25 | 8 | activates 2026-07-20 to 2026-07-24 UTC | `order_ref` | 0 | 0 |
| `agents/agent_0/orders/previous.csv` | 72 | 0 | 8 | empty | `order_ref` | 0 | 0 |

The 12 top-level `data/*.csv` files total 6,167,166 bytes (5.881 MiB).

### Exact narrow headers

```text
cme_swap_data.csv:
date,ticker,price,dv01

treasury_futures_data.csv:
date,ticker,price,dv01

swap_rates.csv:
date,eris_swap_2y_price,eris_swap_2y_return,
eris_swap_5y_price,eris_swap_5y_return

treasury_futures.csv:
date,treasury_futures_2y_price,treasury_futures_2y_return,
treasury_futures_5y_price,treasury_futures_5y_return

treasury_rates.csv:
date,dgs1mo,dgs2mo,dgs3mo,dgs4mo,dgs6mo,dgs1,dgs2,dgs3,dgs5,
dgs7,dgs10,dgs20,dgs30,sofr,effr

r2_objects.csv:
bucket,object_key,filename,folder,database_name,size_bytes,last_modified,
etag,storage_class

Agent order CSVs:
order_ref,activate_at,symbol,side,quantity,status,contract_id,order_id
```

### Wide derived-column families

`raw_price_data.csv` has:

- `date`
- 13 Treasury curve fields from `dgs1mo` through `dgs30`
- `sofr`, `effr`
- 2Y/5Y Eris price and return
- 2Y/5Y Treasury-futures price and return

`signal_data.csv` carries all 24 raw columns plus:

```text
funding_spread_proxy_bps
funding_spread_proxy_mean_bps
eris_swap_2y_price_price_z
eris_swap_2y_price_residual_vs_treasury
eris_swap_2y_price_residual_z
proxy_signal_2y
proxy_position_2y
eris_swap_5y_price_price_z
eris_swap_5y_price_residual_vs_treasury
eris_swap_5y_price_residual_z
proxy_signal_5y
proxy_position_5y
best_proxy_maturity
best_proxy_abs_z
proxy_rank_2y
proxy_rank_5y
```

`risk_data.csv` carries the 40 signal columns plus, for 2Y and 5Y, realized
volatility, signal-strength scale, swap/Treasury leg directions, block
reasons, floating and rounded contract quantities, cap flags, estimated
notionals, Treasury root, and final portfolio `risk_block_reason` and
`risk_allowed`. It has no DV01-named output columns even though DV01 master
data is consumed internally.

The backtest CSVs widen these inputs with current market prices, prior
positions, per-leg P&L, gross/net P&L, swap and Treasury turnover, per-maturity
costs, daily totals, equity, return, and drawdown. Older 85/99-column variants
also contain inactive 10Y/30Y result placeholders. The shapes are not one
stable result schema.

### Cache inventory

`data/cache/` contains 1,491 files totaling 310,840,456 bytes (296.441 MiB):

| Group | Files | Bytes or coverage | Tabular findings |
|---|---:|---|---|
| Eris SOFR settlement CSVs | 1,474 | 305,579,815 bytes; filename dates 2020-09-04 to 2026-07-15 | 463,698 rows; 64 columns; two column-order variants; no duplicate headers; no exact duplicate rows |
| NY Fed EFFR JSON | 3 | Full and short cache windows through 2026-07-15 | Not treated as canonical tabular files |
| NY Fed SOFR JSON | 3 | Full and short cache windows through 2026-07-15 | Not treated as canonical tabular files |
| Treasury yield-curve XML | 9 | One file per year, 2018 through 2026 | Not treated as canonical tabular files |
| Yahoo ZT/ZF JSON | 2 | 2018-01-01 through 2026-07-15 request windows | Continuous-root research source |

The Eris files have 86 to 536 rows each and no empty files. Their two schemas
contain the same 64 field names in different order; `PV01`/`DV01` placement is
the material ordering difference. `EvaluationDate + Symbol` is not unique in
20 files, with 4,241 rows beyond the first occurrence. The full rows are not
exact duplicates, so Phase 2 must identify the correct vendor key and explain
the repeated symbol observations rather than dropping them blindly.

### R2 inventory aggregate

The ignored `r2_objects.csv` records:

- 2,117 object rows from 1 bucket
- 0 duplicate `bucket + object_key` rows
- 144 distinct folder strings
- 513,045,140,700 total bytes (477.811 GiB)
- modification times from 2026-06-26 through 2026-07-14 UTC
- 0 recognized database names; all `database_name` fields are empty

No object bodies were read during P00 and object names are intentionally not
listed in this report.

### Obvious local lineage

```text
Treasury XML + NY Fed JSON
    -> treasury_rates.csv

Eris settlement CSV cache
    -> cme_swap_data.csv (date,ticker,price,dv01)
    -> swap_rates.csv (wide, back-adjusted strategy prices)

Yahoo continuous ZT/ZF JSON
    + CME fixed Eris/Treasury hedge ratios
    -> treasury_futures_data.csv (date,ticker,price,proxy dv01)
    -> treasury_futures.csv (wide, back-adjusted strategy prices)

treasury_rates.csv + swap_rates.csv + treasury_futures.csv
    -> raw_price_data.csv
    -> signal_data.csv

signal_data.csv + CME/Treasury master DV01 data
    -> risk_data.csv
    -> backtest result CSVs

risk_data.csv
    -> Agent 0 sizing caps

R2 metadata listing
    -> r2_objects.csv
    (not consumed by the strategy pipeline)
```

The current tree does not distinguish source, canonical, paper, result, and
manifest directories as required by the target architecture.

## 5. Signal implementation versus the hypothesis

### What exists

- Treasury CMT-like public curve observations (`dgs2`, `dgs5`, etc.).
- SOFR and EFFR observations.
- Eris 2Y/5Y SOFR swap-futures settlement prices and contract DV01.
- Continuous public ZT/ZF prices.
- `funding_spread_proxy_bps = (EFFR - SOFR) × 100`.
- A 252-observation rolling mean of that funding proxy.
- A rolling regression residual of Eris futures price on Treasury futures
  price, followed by a rolling z-score.
- Entry at absolute z-score 2.0 and exit hysteresis at 0.5.
- 2Y/5Y proxy ranking by absolute residual z-score.
- DV01-based sizing, volatility/signal-strength scaling, gross/net DV01 checks,
  and contract rounding.

### What the active signal actually uses

`add_proxy_signal` chooses:

```text
rolling z-score(
    Eris swap-futures price
    - rolling regression on Treasury-futures price
)
```

If the Treasury-futures price is absent, it falls back to the Eris price
z-score. The funding-proxy columns are calculated but not consumed by this
entry/exit decision.

The rolling mean, variance, regression, and z-score include the current row.
The code has no explicit observation/publication/decision/fill timestamp
contract, so whether this is causal depends on an unstated decision-after-close
assumption.

### Hypothesis classification

| Component | Current classification | Gap |
|---|---|---|
| `CMS - CMT` fixed swap spread | Unavailable/not implemented | No maturity-matched swap-rate field is used |
| `L - repo` funding spread | Proxy | EFFR-SOFR is labelled a proxy; repo/collateral consistency is absent |
| Expected future funding burden | Proxy/not integrated | Rolling historical proxy mean exists but is not used by the active signal |
| Gross/net excess spread | Not implemented | No economic excess-spread equation or directional cost buffer |
| Z-score | Price-residual proxy | Standardizes a futures-price residual, not economic excess spread |
| Economic entry eligibility | Not implemented | Entry depends only on proxy z-score |
| Reverse trade | Proxy state sign | Economic leg interpretation is not validated |
| Cross-maturity ranking | Proxy | Ranks absolute residual z-scores for 2Y/5Y only |
| DV01 hedge | Partial/proxy | Eris DV01 is observed; Treasury DV01 usually derives from fixed CME ratios rather than CTD |
| Volatility sizing/risk | Partial implementation | Uses proxy residual volatility and current configuration |

No current P&L result is a test of the complete economic swap-spread
hypothesis.

## 6. Backtest mechanics and limitations

### Timing and fills

- The engine replays daily rows, not timestamped market events.
- Current-row price changes earn P&L on prior-row signed contract quantities
  through `.shift(1)`.
- Current-row contract changes therefore affect the next observed price
  change.
- There are no explicit order intents, order timestamps, next-eligible fills,
  bid/ask prices, rejects, partial fills, or broker positions.
- The result is a deterministic contract-quantity replay, not an execution
  simulation.

### Costs

`BacktestConfig` defaults:

```text
swap_cost_bps = 0.0
treasury_cost_per_contract = 0.0
```

CLI defaults are also zero. The implemented cost is:

```text
abs(change in swap notional) × swap_cost_bps / 10,000
+ abs(change in Treasury contracts) × treasury_cost_per_contract
```

There is no observed directional bid/ask, commission, slippage, financing,
liquidity impact, or explicit conservative missing-cost behavior.

### Roll behavior

- Strategy price series are back-adjusted when the selected ticker changes.
- Returns on the roll row are set to zero in the wide strategy files.
- In the backtest master, cross-contract price change is masked to zero.
- Roll turnover is counted as absolute close quantity plus absolute open
  quantity for both legs.
- With default zero costs, that recorded turnover has no P&L effect.

### Source limitations

- Yahoo symbols are continuous public roots, not executable contract months.
- Default Treasury DV01 is `cme_fixed_ics_ratio_proxy`, not CTD-derived risk.
- Code supports a licensed CTD input path, but the current master is identified
  as proxy and warns that settlement, CTD, roll P&L, and basis risk are not
  production validated.
- Eris selection uses public settlement files and a DV01-nearness heuristic
  instead of an approved roll calendar.
- Current universe is only 2Y/5Y.
- Backtest outputs combine inputs, features, positions, P&L, and diagnostics in
  85/99-column files without run manifests or stable schema versions.

## 7. Current R2/Quantt and IBKR capabilities

### R2/Quantt

Present:

- Untracked `r2_database_names.py` uses the S3 API to list buckets/objects and
  export metadata.
- It requires five named R2 environment variables and performs no object-body
  reads.
- Existing `r2_objects.csv` provides aggregate metadata for 2,117 objects.
- The committed standalone `cloudflare_r2_test.py` previously listed buckets
  and object keys, but is currently staged for deletion.

Absent:

- Reproducible boto3/python-dotenv dependencies
- Selective Quantt object sampling or ingestion
- Schema discovery/validation of strategy-relevant objects
- Field-level lineage
- Canonical conversion and manifests
- Credential-redacted, committed verification evidence

P00 made no Cloudflare request.

### IBKR

Present in Agent 0:

- Localhost connection to hard-coded paper port 7497
- `DU` prefix and managed-account validation
- Futures contract-detail lookup and qualification
- Open-order inspection and capacity allocation
- What-if margin preview with a 10% reserve
- Market `DAY` orders with `GoodAfterTime`
- Order submission and global cancellation
- Local upcoming/previous CSV tracking

Absent:

- Fake-broker test coverage
- Default dry-run
- Current quote recorder
- Fill, execution, and position capture
- Reconciliation telemetry sufficient for immutable paper-run evidence
- Shared paper-agent platform
- Canonical IBKR paper schemas and run manifests

P00 made no IBKR connection and submitted or cancelled zero orders.

## 8. Facts requiring user decisions

MG0 decision:

1. Approve this baseline as sufficient evidence to begin environment repair
   under P01, or return specific factual corrections.

Required by MG1 after P01/P02 evidence:

2. Choose the authoritative Agent 0 frequency. Current executable behavior is
   25/week; documentation also claims 50, 100, and 250.
3. Confirm that Agent 0's frozen initial universe is the four roots currently
   representable in code (YIT, YIW, ZT, ZF), with ZN/ZB treated as stale[
   documentation rather than silently added behavior.

Repository-policy decision:

4. Decide whether required new `docs/` artifacts should be tracked. The current
   user-owned `.gitignore` rule ignores this P00 report, all future verification
   records, and other new master-plan outputs.

Operational fact for later reconciliation:

5. The local upcoming-order file still contains 25 accepted records for a past
   week while the previous-order file is empty. No broker check occurred in
   P00; this state must not be assumed reconciled.

## 9. Reproduction notes

All commands were run from the repository root. Git commands used a per-command
safe-directory override because the sandbox user does not own the checkout:

```powershell
git -c safe.directory='C:/Users/jaydo_0v7vk2o/Downloads/Swap_Arbitrage' `
  branch --show-current
git -c safe.directory='C:/Users/jaydo_0v7vk2o/Downloads/Swap_Arbitrage' `
  log -10 --date=short --pretty=format:'%h %ad %s'
git -c safe.directory='C:/Users/jaydo_0v7vk2o/Downloads/Swap_Arbitrage' `
  status --short
git -c safe.directory='C:/Users/jaydo_0v7vk2o/Downloads/Swap_Arbitrage' `
  check-ignore -v .env .venv data/cache r2_objects.csv `
  agents/agent_0/orders/upcoming.csv agents/agent_0/orders/previous.csv
```

CSV evidence used Python 3.12.13 with pandas to read headers and rows, infer
the candidate keys stated in the table, count duplicates, and parse date
coverage. Raw Eris verification streamed every cache CSV using the standard
library `csv` module; it compared raw headers and complete row tuples without
rewriting files.

Secret values, R2 object keys, IBKR account identifiers, contract identifiers,
and order identifiers are intentionally omitted.
