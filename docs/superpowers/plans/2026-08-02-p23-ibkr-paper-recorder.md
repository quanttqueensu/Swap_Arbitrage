# P23 IBKR Paper Recorder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a paper-only, fake-tested IBKR recorder that writes validated quote, order, fill, and position CSVs without representing submission or cancellation.

**Architecture:** `PaperEventStore` owns contained, atomic, contract-validated CSV persistence. `IbkrPaperRecorder` owns session safety and broker-object normalization, depends on an injected IB-like object and clock, and exposes only read/record methods.

**Tech Stack:** Python 3.12, standard-library `csv`, `dataclasses`, `datetime`, `decimal`, `pathlib`, existing `data_pipeline.contracts`, `unittest`, and `ib_insync`-shaped fakes.

## Global Constraints

- Use only IBKR paper host `127.0.0.1`, port `7497`, and `DU`-prefixed accounts.
- Automated development uses fake brokers and a socket guard; broker-order submissions and cancellations remain zero.
- Account IDs, credentials, hosts, and client IDs never enter recorder CSVs or exception text.
- Every output uses the exact P21 schema, unique key, ordering, and UTC format.
- Preserve unrelated working-tree changes and Agent 0 behavior.

---

### Task 1: Atomic schema-driven paper event store

**Files:**
- Create: `data_pipeline/paper_store.py`
- Create: `tests/test_ibkr_paper_recorder.py`

**Interfaces:**
- Consumes: `SCHEMAS: dict[str, CsvContract]` and `validate_csv(contract, path) -> int`.
- Produces: `PaperEventStore(root: Path, agent_id: str, run_id: str)` and `write(schema_id: str, rows: Iterable[Mapping[str, object]]) -> int`.

- [ ] **Step 1: Write the failing store tests**

Create a socket-guarded `tests/test_ibkr_paper_recorder.py` with literal quote rows. Assert that two writes merge by `timestamp_utc,instrument_id`, sort deterministically, and produce the exact header. Add tests that reject `../run`, conflicting duplicate keys, unsupported schemas, and a patched `Path.replace` failure while preserving the old destination bytes.

```python
def test_store_merges_idempotently_and_sorts(self) -> None:
    store = PaperEventStore(self.root, "agent_0", "run-1")
    rows = [
        quote("2026-08-02T15:00:01Z", "IBKR:2", "99", "100"),
        quote("2026-08-02T15:00:00Z", "IBKR:1", "98", "99"),
    ]
    self.assertEqual(store.write("paper_quotes", rows), 2)
    self.assertEqual(store.write("paper_quotes", [rows[0]]), 2)
    self.assertEqual(validate_csv(SCHEMAS["paper_quotes"], store.path_for("paper_quotes")), 2)
```

- [ ] **Step 2: Run the store tests and verify RED**

Run: `python -m unittest tests.test_ibkr_paper_recorder.PaperEventStoreTests -v`

Expected: import failure because `data_pipeline.paper_store` does not exist.

- [ ] **Step 3: Implement the minimal event store**

Implement a fixed schema-to-filename mapping, strict ID regex `^[A-Za-z0-9_.-]+$`, resolved-path containment, scalar serialization (`datetime` to UTC `Z`, `Decimal` without exponent drift), existing-row loading, immutable duplicate comparison, order-status-only reconciliation for `paper_orders`, sorting by the contract ordering, temporary-sibling writing, `validate_csv`, and `Path.replace`.

```python
SCHEMA_FILES = {
    "paper_quotes": "quotes.csv",
    "paper_orders": "orders.csv",
    "paper_fills": "fills.csv",
    "paper_positions": "positions.csv",
}
```

The constructor stores a resolved root plus validated agent/run IDs;
`path_for` and `write` accept only the four `SCHEMA_FILES` keys.

- [ ] **Step 4: Run Task 1 tests and full schema tests**

Run:

```powershell
python -m unittest tests.test_ibkr_paper_recorder.PaperEventStoreTests -v
python -m unittest tests.test_schema_contracts -v
```

Expected: both commands pass.

- [ ] **Step 5: Commit Task 1**

```powershell
git add data_pipeline/paper_store.py tests/test_ibkr_paper_recorder.py
git commit -m "feat: add atomic paper event store"
```

### Task 2: Paper session guard and quote recording

**Files:**
- Create: `data_pipeline/ibkr_paper_source.py`
- Modify: `tests/test_ibkr_paper_recorder.py`

**Interfaces:**
- Consumes: `PaperEventStore.write`, an IB-like object with `isConnected`, `managedAccounts`, and `reqMktData`, and qualified contracts with positive `conId`.
- Produces: `PaperSessionConfig`, `IbkrPaperRecorder.validate_session()`, `request_quotes(contracts)`, and `record_quote(contract, ticker, observed_at_utc) -> int`.

- [ ] **Step 1: Write failing guard and quote tests**

Use `FakeIB` call lists and `SimpleNamespace` broker records. Cover every unsafe configuration mutation, disconnected sessions, managed-account mismatch, positive stable ID `IBKR:<conId>`, UTC conversion, stale timestamps, NaN/nonpositive/crossed quotes, account redaction, and proof that `reqMktData` is not reached after a guard failure.

```python
def test_unsafe_configuration_blocks_before_broker_access(self) -> None:
    recorder = recorder_for(port=7496, account_id="DU_SECRET")
    with self.assertRaisesRegex(PaperSafetyError, "paper port") as caught:
        recorder.request_quotes([contract(101)])
    self.assertNotIn("DU_SECRET", str(caught.exception))
    self.assertEqual(recorder.ib.market_data_requests, [])
```

- [ ] **Step 2: Run quote tests and verify RED**

Run: `python -m unittest tests.test_ibkr_paper_recorder.PaperRecorderQuoteTests -v`

Expected: import failure because `data_pipeline.ibkr_paper_source` does not exist.

- [ ] **Step 3: Implement guard and quote normalization**

Create immutable `PaperSessionConfig`, `PaperSafetyError`, `_instrument_id`, `_utc_text`, `_finite_decimal`, `_redact`, and `IbkrPaperRecorder`. Every public method begins with `validate_session`. Reject quote age `< 0` or `> stale_after_seconds`. Pass normalized rows to `paper_quotes`.

```python
@dataclass(frozen=True)
class PaperSessionConfig:
    host: str
    port: int
    client_id: int
    account_id: str
    account_alias: str
    paper_only: bool = True
    live_trading_enabled: bool = False
    stale_after_seconds: int = 30
```

- [ ] **Step 4: Run quote and store tests**

Run: `python -m unittest tests.test_ibkr_paper_recorder -v`

Expected: all current tests pass without a network attempt.

- [ ] **Step 5: Commit Task 2**

```powershell
git add data_pipeline/ibkr_paper_source.py tests/test_ibkr_paper_recorder.py
git commit -m "feat: guard IBKR paper quote recording"
```

### Task 3: Order, fill, and position reconciliation

**Files:**
- Modify: `data_pipeline/ibkr_paper_source.py`
- Modify: `data_pipeline/paper_store.py`
- Modify: `tests/test_ibkr_paper_recorder.py`

**Interfaces:**
- Consumes: broker-shaped `order`, `execution`, `commission_report`, and position records.
- Produces: `record_order(decision_id: str, contract: object, order: object, status: str, created_at_utc: datetime) -> int`, `record_fill(order_ref: str, contract: object, execution: object, commission_report: object) -> int`, and `record_positions(position_rows: Iterable[object], observed_at_utc: datetime) -> int`.

- [ ] **Step 1: Write failing normalization and reconciliation tests**

Cover BUY-positive/SELL-negative quantities, nullable broker order ID, allowed order status update, forbidden immutable-order mutation, duplicate fill callback, conflicting execution ID, two partial fills with distinct IDs, nonnegative commission, one-timestamp position snapshots, and duplicate instruments in a snapshot.

```python
def test_duplicate_fill_callback_is_idempotent(self) -> None:
    first = self.recorder.record_fill("o-1", contract(101), execution("e-1", 1), commission("1.20"))
    second = self.recorder.record_fill("o-1", contract(101), execution("e-1", 1), commission("1.20"))
    self.assertEqual((first, second), (1, 1))
```

- [ ] **Step 2: Run reconciliation tests and verify RED**

Run: `python -m unittest tests.test_ibkr_paper_recorder.PaperRecorderEventTests -v`

Expected: `AttributeError` for the unimplemented record methods.

- [ ] **Step 3: Implement event normalization**

Use exact schema field names. Require nonempty `orderRef`, `decision_id`, execution ID, order status, order type, and time in force. Derive signed quantities from side. Serialize execution time as UTC. Treat a missing commission report as invalid rather than zero. Reject account IDs found in any broker object before storage.

```python
side = str(order.action).upper()
unsigned_quantity = int(order.totalQuantity)
signed_quantity = unsigned_quantity if side == "BUY" else -unsigned_quantity
row = {
    "order_ref": str(order.orderRef),
    "decision_id": decision_id,
    "created_at_utc": _utc_text(created_at_utc),
    "instrument_id": _instrument_id(contract),
    "side": side,
    "quantity": signed_quantity,
    "order_type": str(order.orderType),
    "time_in_force": str(order.tif),
    "status": status,
    "ibkr_order_id": str(order.orderId) if int(order.orderId or 0) > 0 else "",
}
return self.store.write("paper_orders", [row])
```

- [ ] **Step 4: Run P23 tests and import smoke**

Run:

```powershell
python -m unittest tests.test_ibkr_paper_recorder -v
python -m unittest tests.test_import_smoke tests.test_agent_0_characterization -v
```

Expected: all pass; Agent 0 behavior remains unchanged.

- [ ] **Step 5: Commit Task 3**

```powershell
git add data_pipeline/ibkr_paper_source.py data_pipeline/paper_store.py tests/test_ibkr_paper_recorder.py
git commit -m "feat: record IBKR paper events"
```

### Task 4: P23 verification evidence

**Files:**
- Create: `docs/verification/P23.md`

**Interfaces:**
- Consumes: P23 test output, commit IDs, and review findings.
- Produces: one immutable P23 evidence record handed to P24.

- [ ] **Step 1: Run P23 safety scans and complete test suite**

Run:

```powershell
rg -n "placeOrder|reqGlobalCancel|cancelOrder|account_id|DU_" data_pipeline/ibkr_paper_source.py data_pipeline/paper_store.py tests/test_ibkr_paper_recorder.py
python -m unittest discover -s tests -v
git diff --check
```

Expected: broker-mutating methods are absent from production recorder modules; account-like literals occur only in tests; all tests pass; diff check exits zero.

- [ ] **Step 2: Write the evidence record**

Record objective, scope, commits, exact commands/results, schema samples generated in temporary directories, fake-broker proof, redaction proof, network/order/cancel counts of zero, reviewer findings and resolutions, unrelated changes preserved, and the explicit statement that MG4 is not requested until P24 completes.

- [ ] **Step 3: Commit P23 evidence**

```powershell
git add -f docs/verification/P23.md
git commit -m "docs: verify P23 paper recorder"
```
