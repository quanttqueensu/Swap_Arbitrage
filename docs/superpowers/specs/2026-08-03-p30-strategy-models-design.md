# P30 immutable strategy models design

## Goal and scope

Create the Phase 4 `strategy` package boundary required by P30: immutable,
validated records shared by later strategy, backtest, and paper-adapter work.
This phase defines data only. It does not implement equations, rolling state,
position construction, risk policy, broker conversion, or migration of current
runtime behavior.

P30 starts from the locally available MG4 facts: canonical rate, futures,
market, and contract-risk partitions exist in the five-folder durable layout;
their executable schemas and deterministic canonicalizer tests are available.
The earlier P24 workflow lacks a completed MG4 verification document, so P30
does not claim that missing historical sign-off. The user authorized local
step approval, and P30 remains isolated from file reads and data migration.

## Chosen structure

Add `strategy/__init__.py` and `strategy/models.py`. Use Python 3.12 standard
library only: frozen, slotted dataclasses; `Enum`/`IntEnum`; `datetime`; and
`Decimal`. Tests live at `docs/tests/test_strategy_models.py`, consistent with
the repository's current test layout.

The six public boundary records are:

- `MarketSnapshot`: one causal view at `decision_time_utc`, containing immutable
  tuples of rate observations, instrument observations, contract metadata,
  paper positions, and working orders.
- `SpreadObservation`: one maturity's spread components, directional costs and
  opportunities, standardized signal inputs, source-quality flag, and
  freshness flag at an observation timestamp.
- `SignalDecision`: decision identity, maturity and UTC decision time, prior
  and new states, direction, one reason code, the exact feature values used,
  and strategy/configuration versions.
- `TargetPosition`: maturity/instrument identities, signed swap and Treasury
  quantities, target/gross/residual DV01, expected turnover and cost, and
  rounding/cap diagnostics.
- `RiskDecision`: allowed flag, scale, immutable reason-code tuple, flatten
  request and urgency, plus the exact named limits and measured values used.
- `OrderIntent`: run/agent/strategy/decision identities, instrument, side,
  absolute quantity, order type, time in force, UTC earliest-submission,
  activation, and expiry bounds, reference price, maximum slippage, and mandatory
  `paper_only=True`.

Small frozen value records (`RateObservation`, `InstrumentObservation`,
`ContractMetadata`, `PaperPosition`, `WorkingOrder`, and `NamedValue`) make the
nested fields explicit. They are concrete values, not an inheritance or
registration framework. `MarketSnapshot` converts supplied iterables to tuples
at construction so callers cannot retain a mutable list behind an otherwise
frozen record.

## Domains and validation

`PositionState` and `TradeDirection` are integer enums using the contract
values `-1`, `0`, and `+1`. `OrderSide`, `OrderType`, `TimeInForce`, and
`FlattenUrgency` are string enums with only the immediately consumed values.
Callers must supply enum members; raw strings and integers fail closed.

Field names carry units (`_bps`, `_usd`, `_usd_per_bp`, `_price_points`,
`_contracts`, `_utc`). Decimal quantities must be finite. Values described as
absolute quantities, costs, counts, limits, slippage, and gross/target amounts
must be nonnegative; signed hedge quantities and residual net DV01 may be
negative. Risk scale is inclusive in `[0, 1]`. Required identifiers, maturity,
versions, diagnostics, sources, and reason codes are nonblank.

Every datetime must be timezone-aware UTC. Ordered intent timestamps must
satisfy earliest submission no later than activation and activation no later
than expiry. `paper_only` must be the literal boolean `True`. Reason-code
collections are tuples and reject blank or duplicate codes.

## Stable CSV serialization

Expose `to_csv_row(record) -> dict[str, str]`. It returns fields in dataclass
declaration order and recursively emits stable scalar text:

- enums use their declared values;
- UTC datetimes use ISO 8601 with a terminal `Z`;
- `Decimal` uses fixed-point text without exponent notation;
- booleans use lowercase `true`/`false`;
- `None` uses an empty field;
- tuples and nested records use compact JSON with declared field order.

Serialization performs no I/O and returns a fresh dictionary. This is the one
CSV-facing helper; individual records do not duplicate `as_row` methods.

## Dependencies and boundaries

`strategy.models` imports only Python standard-library modules. It does not
import `data_pipeline`, current root pipelines, a broker library, a file API,
the clock, or network code. Current runtime modules remain unchanged. Later
P31-P35 modules consume these records directly, while later adapters translate
them to their own CSV or broker formats.

## Testing and completion evidence

Tests begin red against the absent package and then cover every record's
immutability, UTC enforcement, enum domains, unit/range validation, immutable
reason codes, tuple normalization, mandatory paper-only marker, timestamp
ordering, and literal serialization examples. P30 completion also requires the
focused model tests, both repository test suites, compilation, `git diff
--check`, an interface review against `PROJECT_CONTRACTS.md`, and a simplicity
review limited to unnecessary abstraction.
