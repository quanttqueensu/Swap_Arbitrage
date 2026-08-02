# P23 IBKR Paper Recorder Design

## Scope

P23 adds a paper-only IBKR data adapter and a schema-driven event store for
quotes, orders, fills, and positions. It records broker facts; it does not
generate signals, size positions, submit orders, cancel orders, or connect to a
live account during development. P23 ends with fake-broker evidence ready for
P24 and MG4.

## Architecture

`data_pipeline/paper_store.py` owns deterministic CSV persistence. It receives
rows already normalized to a `CsvContract`, merges them by the contract's
unique key, rejects conflicting duplicates, sorts by the contract ordering,
writes a temporary sibling, validates it with `validate_csv`, and atomically
replaces the destination.

`data_pipeline/ibkr_paper_source.py` owns the IBKR boundary. It validates a
`PaperSessionConfig` before reading broker state, maps qualified contracts to
stable `IBKR:<conId>` instrument IDs, normalizes broker objects into the four
approved paper schemas, and hands rows to `PaperEventStore`. The adapter is
constructed with an IB-like object and a UTC clock so tests use a complete fake
without patching production internals.

The existing Agent 0 broker remains unchanged. P23 creates the recorder used by
future shared-agent work; it does not retrofit Agent 0 order submission or move
P50 responsibilities forward.

## Interfaces

`PaperSessionConfig` is an immutable record with `host`, `port`, `client_id`,
`account_id`, `account_alias`, `paper_only`, `live_trading_enabled`, and
`stale_after_seconds`. Validation requires host `127.0.0.1`, port `7497`, a
positive client ID, `paper_only=True`, `live_trading_enabled=False`, an account
starting with `DU`, a nonempty alias, and a positive stale threshold. Errors
name the alias and never the account ID.

`PaperEventStore(root, agent_id, run_id)` exposes `write(schema_id, rows)`. It
resolves only these paths beneath `root/agent_id/run_id`: `quotes.csv`,
`orders.csv`, `fills.csv`, and `positions.csv`. Agent and run IDs accept only
letters, digits, `_`, `-`, and `.`; resolved paths must remain beneath `root`.

`IbkrPaperRecorder(ib, config, store, clock)` exposes:

- `validate_session()` for the paper configuration, active connection, and
  managed-account membership;
- `request_quotes(contracts)` after validation, using `reqMktData` only;
- `record_quote(contract, ticker, observed_at_utc)`;
- `record_order(decision_id, contract, order, status, created_at_utc)`;
- `record_fill(order_ref, contract, execution, commission_report)`;
- `record_positions(position_rows, observed_at_utc)`.

The adapter deliberately has no `submit`, `placeOrder`, `cancel`, or
`reqGlobalCancel` method.

## Normalization and Idempotency

All timestamps are timezone-aware and serialized as UTC with a trailing `Z`.
Quote timestamps older than `stale_after_seconds` relative to the injected
clock are rejected. Bid, ask, and sizes must be finite and positive; crossed
quotes are rejected.

Order sides are `BUY` or `SELL`; quantities are signed positive for `BUY` and
negative for `SELL`. `orderRef` is the canonical order reference and the broker
order ID is nullable until acknowledgement. Recording the same order reference
updates status and acknowledgement ID but cannot change decision, instrument,
side, quantity, order type, or time in force.

Execution ID is the fill ID. Duplicate callbacks with identical normalized
content are no-ops; a duplicate key with different content is an error. Partial
fills are distinct when IBKR supplies distinct execution IDs. Commission is a
finite nonnegative USD value. Position snapshots use one shared observation
timestamp and reject duplicate instruments within a snapshot.

## Safety and Redaction

Every public adapter method calls the session guard before broker access or
storage. No CSV contains credentials, account IDs, host names, or client IDs.
Exceptions redact the configured account ID if it appears in an upstream
message. Automated tests install a module-lifetime socket guard before importing
the adapter and assert that unsafe configuration fails before the fake broker
receives any call.

## Testing

Tests use a complete fake IB object and literal broker-shaped records. They
cover paper-account and production-port rejection, managed-account mismatch,
stable instrument IDs, quote normalization, stale and crossed quotes, duplicate
callbacks, order status reconciliation, partial fills, commissions, position
snapshots, atomic-write failure, path containment, and account redaction.
Tests also inspect the adapter's public surface to prove that order submission
and cancellation are not representable.

## Deliverables

- `data_pipeline/paper_store.py`
- `data_pipeline/ibkr_paper_source.py`
- `tests/test_ibkr_paper_recorder.py`
- `docs/verification/P23.md`

