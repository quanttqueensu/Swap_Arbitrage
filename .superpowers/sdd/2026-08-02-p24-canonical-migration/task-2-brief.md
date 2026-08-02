### Task 2: Pure rate and futures canonicalizers

**Files:**
- Create: `data_pipeline/canonicalize.py`
- Create: `tests/test_canonical_migration.py`

**Interfaces:**
- Consumes: CSV `Mapping[str, str]` rows and literal source timing metadata.
- Produces: immutable effective-dated `SourceTiming`, `canonicalize_rates(path: Path) -> dict[int, list[dict[str, str]]]`, `canonicalize_futures(swap_path: Path, treasury_path: Path) -> FuturesCanonicalization`, and `canonicalize_daily_market(swap_prices_path: Path, treasury_prices_path: Path, timing_rules: Mapping[str, tuple[SourceTiming, ...]]) -> dict[int, list[dict[str, str]]]`. `FuturesCanonicalization` exposes immutable named `settlements_by_year` and `risk_by_year` partitions because their exact schemas are incompatible.

- [ ] **Step 1: Write failing literal-fixture tests**

Create tiny rate, swap, Treasury-futures, swap-price, and Treasury-price CSVs. Assert exact output rows, basis-point conversion (`4.10` percent to `410` bp), provider identities (`UST`, `NYFED`, `ERIS`, `YAHOO`), stable instrument IDs, blank settlement DV01, contract-risk DV01, proxy labels, sorting, and rejection of duplicate, missing, nonfinite, nonpositive, or unknown columns.

```python
def test_rates_preserve_provider_and_convert_percent_to_basis_points(self) -> None:
    partitions = canonicalize_rates(self.fixture("treasury_rates.csv"))
    self.assertEqual(partitions[2026][0], {
        "observation_date": "2026-08-01",
        "source": "UST",
        "series_id": "DGS2",
        "maturity": "2Y",
        "rate_bps": "410",
    })
```

- [ ] **Step 2: Run canonicalizer tests and verify RED**

Run: `python -m unittest tests.test_canonical_migration.CanonicalizerTests -v`

Expected: import failure because `data_pipeline.canonicalize` does not exist.

- [ ] **Step 3: Implement minimal pure transforms**

Use `Decimal` for all numeric conversion. Declare exact source-column maps for only consumed 2Y/5Y rates and SOFR/EFFR. Require exact headers. Build immutable expiry-aware Eris IDs from ticker; retain root-only Treasury data as explicitly labelled continuous proxies. Set literal availability-time rules from the approved source matrix and reject dates for which no rule exists.

```python
class CanonicalizationError(ValueError):
    pass

@dataclass(frozen=True)
class SourceTiming:
    effective_from: date
    effective_to: date
    observation_time_utc: time
    availability_delay: timedelta
    source: str
    classification: str
    proxy_label: str = ""

RATE_COLUMNS = {
    "dgs2": ("UST", "DGS2", "2Y"),
    "dgs5": ("UST", "DGS5", "5Y"),
    "sofr": ("NYFED", "SOFR", "ON"),
    "effr": ("NYFED", "EFFR", "ON"),
}

def percent_to_bps(value: str) -> str:
    parsed = Decimal(value)
    if not parsed.is_finite():
        raise CanonicalizationError("rate must be finite")
    return format(parsed * Decimal("100"), "f").rstrip("0").rstrip(".")
```

- [ ] **Step 4: Materialize and validate fixture partitions**

Write fixture outputs with a small test helper, then call `validate_csv` for `historical_rates`, `historical_futures_settlements`, `contract_risk`, and `daily_market`.

Run: `python -m unittest tests.test_canonical_migration.CanonicalizerTests -v`

Expected: all pass.

- [ ] **Step 5: Commit Task 2**

```powershell
git add data_pipeline/canonicalize.py tests/test_canonical_migration.py
git commit -m "feat: canonicalize approved market inputs"
```
