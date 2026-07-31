# P10 Strategy Equations

**Status:** Proposed research contract pending MG2
**Executable universe:** 2Y and 5Y only
**Source verification date:** 2026-07-31

## Scope and non-goals

P10 is a documentation-and-test research contract. It separates the economic
swap-spread hypothesis from the futures basket that may implement an approved
direction, and it stops at MG2 pending manual approval. No production strategy
behavior changes in P10. In particular, P10 does not access Quantt,
Cloudflare/R2, IBKR, or market-data APIs; submit, cancel, or simulate broker
orders; or create a production strategy, broker, risk, or backtest behavior.

The only approved executable maturity universe is 2Y and 5Y. 10Y/30Y mappings
are unavailable: they must not be inferred from generic economic notation,
published spread products, or similar contracts. An Eris/Treasury futures-price
difference is not an economic swap spread and must not be used to produce one.

All numerical market values and costs in examples are synthetic. They are
illustrative research inputs, not observed market data, live costs, or backtest
assumptions. Exact results require validated maturity, collateral, unit, and
timestamp inputs; proxy-labelled results cannot be relabelled as exact or as a
complete-strategy result.

## Notation, units, and classification

The project-wide unit and sign contract applies throughout this document:

- Rates and spreads are basis points (`_bps`); raw rate decimals are
  (`_decimal`) and are converted once at the input boundary.
- Prices are exchange or source price points (`_price`).
- DV01 is US dollars per one-basis-point parallel rate increase
  (`_dv01_usd_per_bp`); P&L and costs are US dollars (`_usd`).
- Times are UTC ISO 8601 timestamps (`_utc`); research daily observations use
  ISO date.
- Quantities are signed integer contracts: positive is long the exchange
  contract and negative is short.

For a signed quantity \(q_i\), official price multiplier \(M_i\), quoted
price change \(\Delta P_i\), and USD cost \(C_i\), the project P&L convention
is \(\mathrm{PnL}_{i,t}=q_{i,t-1}M_i\Delta P_{i,t}-C_{i,t}\). Rate-sensitivity
sign is represented explicitly; absolute DV01 is permitted for sizing only
after the documented instrument direction has established the signed exposure.

Each input and result is classified using exactly one of the following terms:

```text
exact: directly satisfies the maturity, collateral, unit, and timestamp contract
proxy: a named substitute that cannot be relabelled as exact or complete strategy output
assumed: a declared synthetic or scenario input, never presented as observed
derived: calculated only from classified inputs using a displayed equation
unavailable: absent or unvalidated; blocks the affected exact result or executable basket
```

The classifications are mutually exclusive. Classify a raw observed input, or
an uncalculated pass-through value, as `exact`, `proxy`, `assumed`, or
`unavailable` as its definition requires. Classify a result produced by a
displayed equation as `derived`, even when every input is `exact`; it must also
display the classification of every input as lineage. If a required input is
`unavailable`, do not produce the calculation: the affected result or
executable basket is `unavailable`, not `derived`. A `derived` result with a
`proxy` or `assumed` input is not an exact result or complete strategy output.
`EFFR-SOFR` is a named `proxy` for exact \(L-repo\), not a replacement for that
exact floating funding spread. Synthetic examples use `assumed` values and any
calculation from them is `derived`, with `assumed` input lineage, never
observed.

## Source ledger and convention evidence

All sources in this ledger are direct official primary-source pages or official
documents. They were reopened for implementation verification on 2026-07-31.
The ledger records only the convention supported by each source; it does not
turn a source convention into a current market observation or a sizing input.

| Source ID | Direct official source(s) | Verified convention | Availability boundary |
|---|---|---|---|
| `CME-ERIS-FAQ` | [Eris SOFR Swap Futures FAQ](https://www.cmegroup.com/markets/interest-rates/files/eris-sofr-swap-futures-faq.pdf); [Eris SOFR Swap Futures overview](https://www.cmegroup.com/markets/interest-rates/files/eris-sofr-swap-futures-overview.pdf) | One contract is USD 100,000 notional. A long receives fixed and pays compounded SOFR. The price is indexed to 100, and one price point is USD 1,000. The 2Y prefix is YIT and the 5Y prefix is YIW. | Contract convention only; current contract terms, prices, and DV01 still require validated as-of inputs. |
| `CME-SPREAD-PRIMER` | [Trading Swap Spreads with Futures: a primer for Eris/Treasury swap spreads](https://www.cmegroup.com/articles/2024/trading-swap-spreads-with-futures-a-primer-for-eristreasury-swap-spreads.html) | Buy spread means buy Eris and sell Treasury. The ETU 2Y displayed ratio is 2:1; the EWV 5Y displayed ratio is 1:1. | Published ratios are sanity checks, not the sizing authority. |
| `CME-ETU-NOTICE` | [CME Globex notice, 2024-03-11](https://www.cmegroup.com/notices/electronic-trading/2024/03/20240311.html) | ETU is YIT versus ZT with leg quantity ratio 2:1. | Confirms the published product mapping and ratio only. |
| `CME-EWV-NOTICE` | [CME Globex notice, 2023-11-06](https://www.cmegroup.com/notices/electronic-trading/2023/11/20231106.html) | EWV is YIW versus ZF with leg quantity ratio 1:1. | Confirms the published product mapping and ratio only. |
| `CME-TREASURY-SPECS` | [Understand Treasuries contract specifications](https://www.cmegroup.com/education/courses/introduction-to-treasuries/understand-treasuries-contract-specifications.hideSubnav.educationIframe.html.html?hideAddThisExt=y&hideFooter=y&hideHeader=y&hideRightRail=y); [2-Year T-Note futures contract specs](https://www.cmegroup.com/markets/interest-rates/us-treasury/2-year-us-treasury-note.contractSpecs.html); [5-Year T-Note futures contract specs](https://www.cmegroup.com/markets/interest-rates/us-treasury/5-year-us-treasury-note.contractSpecs.html); [Treasury futures delivery process](https://www.cmegroup.com/content/dam/cmegroup/trading/interest-rates/files/us-treasury-futures-delivery-process.pdf) | ZT face amount/contract factor is USD 200,000/USD 2,000 per point; ZF is USD 100,000/USD 1,000 per point. Long prices fall when yields rise. Delivery and CTD conventions govern current DV01. | Current contract/CTD DV01 is required for executable sizing; contract amounts and quoted multipliers alone are insufficient. |
| `UST-CMT` | [Treasury yield curve methodology](https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics/treasury-yield-curve-methodology); [Interest rates FAQ](https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics/interest-rates-frequently-asked-questions) | CMT is a Treasury par-yield-curve estimate, not a futures yield or futures price. | It may support a maturity-matched Treasury-rate input only when its timestamp and units satisfy the stated economic contract. |
| `NYFED-RATES` | [New York Fed reference rates](https://www.newyorkfed.org/markets/reference-rates) | SOFR, EFFR, and repo-family rates are distinct published reference rates; `EFFR-SOFR` remains a proxy for exact `L-repo`. | The exact maturity/collateral-consistent `L-repo` input is unavailable until validated; do not relabel the proxy as exact. |

The CME page showing `EAT` at 1:1 describes the YIA/YIT Eris-vs-Eris curve
spread, not the `ETU` YIT/ZT Treasury spread. It therefore does not override or
conflict with the ETU 2:1 notice. Published ratios remain sanity checks;
current contract/CTD DV01 remains authoritative for sizing.

## Economic hypothesis

## Causal funding expectation

## Decision clock and movement trigger

## Causal z-score and state rules

## Directional costs and eligibility

## Executable futures direction

## Integer DV01 hedge

## Contract P&L, reversal, roll, and flattening

## Golden calculations

## Availability and proxy matrix

## Fail-closed conditions

## Deliberately unavailable items

## MG2 manual recalculation checklist
