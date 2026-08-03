# Durable data layout

The P23/P24 data work is complete. Approved FRED and CME-derived inputs are
kept under `data/raw_data/`; validated canonical partitions are stored directly
under the five durable domain folders below. There is no staging area,
manifest directory, or migration subsystem.

| Folder | Contents |
|---|---|
| `data/raw_data/` | Original wide inputs, legacy backtest CSVs, and provider cache |
| `data/rates/` | Year-partitioned historical rates |
| `data/futures/` | Year-partitioned futures settlements |
| `data/market/` | Year-partitioned daily market observations |
| `data/contract_risk/` | Year-partitioned contract DV01/risk observations |

Canonicalizers validate headers, types, keys, ordering, provenance, and causal
timing before a partition is written. The retained CSV bytes were moved without
hash changes; consumers use `config.py` for raw inputs and
`data_pipeline/contracts.py` for canonical paths.

The prior P24 manifests and staging trees were temporary workflow artifacts and
have been removed. Git history remains the recovery mechanism for code and
tracked canonical files; raw/provider data remains outside normal Git tracking
as before.
