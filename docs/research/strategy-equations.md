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

For maturity \(m\) at decision time \(t\), with both rate inputs in basis
points:

\[
SS_{m,t}=CMS_{m,t}-CMT_{m,t}
\]

\[
X^{gross}_{m,t}=SS_{m,t}-\widehat{E}_{t}[\overline{FS}_{m}]
\]

\(SS\) is the fixed swap spread. Positive \(X^{gross}\) favours the
traditional receive-fixed/short-Treasury direction; negative \(X^{gross}\)
favours the reverse. An exact result requires exact maturity-matched swap,
Treasury, floating-rate, and collateral-consistent repo inputs. A calculation
from classified inputs is `derived` and keeps the input classifications as
lineage; a missing required input makes the affected output `unavailable`.

## Causal funding expectation

The funding spread is \(FS_t=L_t-repo_t\), in basis points. The frozen
research estimator accepts only the consecutive one-business-day-lagged suffix
available at \(t\). With \(n_t\) such observations,

\[
N_t=\min(60,n_t), \qquad N_t\geq40
\]

\[
\widehat{FS}_{t,h}=\frac{1}{N_t}\sum_{j=1}^{N_t}FS_{t-j}
\quad\text{for }h\in\{1,\ldots,20\}
\]

\[
\widehat{E}_{t}[\overline{FS}_{m}]
=\frac{1}{20}\sum_{h=1}^{20}\widehat{FS}_{t,h}
\]

It is `unavailable` before 40 consecutive lagged observations. The 20
forecast steps are identical, so their horizon average equals the trailing
mean exactly. Forty 5-bp observations produce 5 bp; 60 5-bp observations
also produce 5 bp; 39 observations produce no forecast. Once the history has
60 observations, an older 999-bp value is outside the trailing window and
does not change the 5-bp result. This test-only arithmetic presumes the
history was already selected causally; production calendar validation remains
unavailable pending P11.

## Decision clock and movement trigger

## Causal z-score and state rules

The causal standardized gross opportunity is:

\[
z_{m,t}=\frac{X^{gross}_{m,t}-\mu_{m,t^-}}{\sigma_{m,t^-}}
\]

where \(\mu_{m,t^-}\) and \(\sigma_{m,t^-}\) are respectively the mean and
sample standard deviation of exactly the 252 completed observations preceding
\(t\):

\[
\mu_{m,t^-}=\frac{1}{252}\sum_{i=1}^{252}X^{gross}_{m,t-i},
\qquad
\sigma_{m,t^-}=\sqrt{\frac{\sum_{i=1}^{252}
(X^{gross}_{m,t-i}-\mu_{m,t^-})^2}{251}}
\]

The current observation is excluded. A history with any count other than 252,
or a zero sample standard deviation, produces no z-score. Future observations
cannot revise an already calculated historical z-score; they belong only to a
later decision's prior window.

## Directional costs and eligibility

All six round-trip cost components are explicit USD inputs: swap bid/ask,
Treasury bid/ask, commission/exchange, slippage, roll, and financing not
already included in the expected funding burden. Their directional buffer is:

\[
TC^d_{m,t}=\frac{
C^{swap,d}_{m,t}+C^{Treasury,d}_{m,t}+C^{commission}_{m,t}
+C^{slippage}_{m,t}+C^{roll}_{m,t}+C^{financing}_{m,t}}
{|DV01^{target}_{m,t}|}
\]

The numerator is USD and the denominator is USD/bp, so \(TC\) is bp. The
same financing burden must not occur in both funding and costs. For
\(d\in\{-1,+1\}\):

\[
X^{net,d}_{m,t}=d\,X^{gross}_{m,t}-TC^d_{m,t}
\]

For the approved 2Y/5Y research examples, the additional entry buffer is
0 bp and eligibility is strictly \(X^{net,d}_{m,t}>0\): 0 is ineligible,
while 0.0001 bp is eligible.

## Executable futures direction

## Integer DV01 hedge

## Contract P&L, reversal, roll, and flattening

## Golden calculations

Every numerical input below is synthetic and `assumed`; every shown result is
`derived` with `assumed` lineage. The fixture's `assumed_synthetic` label is a
schema label for that synthetic fixture, not a replacement for the exclusive
input/result classifications above. No calculation below is an observation,
live cost, or backtest assumption.

All four examples use the 60-observation profile \(60\times5\) bp. Its
trailing mean is \((60\times5)/60=5\) bp, and each of its 20 equal forecast
steps is therefore 5 bp; the horizon average remains 5 bp.

### Traditional 2Y (`traditional_2y`, \(d=+1\))

\[
SS=450-420=30, \qquad X^{gross}=30-5=25\ \text{bp}
\]

\[
C=250+250+100+200+100+100=1000\ \text{USD},
\quad TC=1000/1000=1\ \text{bp}
\]

\[
X^{net,+1}=25-1=24\ \text{bp}
\]

The 252 previous values are \(123\times10\), \(123\times20\), 7.5,
22.5, 12.5, 17.5, and \(2\times15\) bp. Their sum is
\(1230+2460+30+30+30=3780\), so \(\mu=3780/252=15\) bp. Squared deviations
total \(123\times25+123\times25+2\times56.25+2\times6.25=6275\), so the
sample standard deviation is \(\sqrt{6275/251}=5\) bp and
\(z=(25-15)/5=2\).

### Traditional 5Y (`traditional_5y`, \(d=+1\))

\[
SS=430-410=20, \qquad X^{gross}=20-5=15\ \text{bp}
\]

\[
C=600+600+400+800+400+200=3000\ \text{USD},
\quad TC=3000/1000=3\ \text{bp},
\quad X^{net,+1}=15-3=12\ \text{bp}
\]

The previous values are \(123\times-5\), \(123\times5\), -7.5, 7.5,
-2.5, 2.5, and \(2\times0\) bp. Their sum is zero, hence \(\mu=0\).
The squared deviations total
\(123\times25+123\times25+2\times56.25+2\times6.25=6275\); therefore
\(\sigma=\sqrt{6275/251}=5\) bp and \(z=(15-0)/5=3\).

### Reverse 2Y (`reverse_2y`, \(d=-1\))

\[
SS=380-420=-40, \qquad X^{gross}=-40-5=-45\ \text{bp}
\]

\[
C=400+400+200+500+300+200=2000\ \text{USD},
\quad TC=2000/1000=2\ \text{bp},
\quad X^{net,-1}=(-1)(-45)-2=43\ \text{bp}
\]

The previous values are \(123\times-35\), \(123\times-15\), -40, -10,
-30, -20, and \(2\times-25\) bp. Their sum is
\(-4305-1845-50-50-50=-6300\), so \(\mu=-6300/252=-25\) bp. Squared
deviations total
\(123\times100+123\times100+2\times225+2\times25=25100\), giving
\(\sigma=\sqrt{25100/251}=10\) bp and
\(z=(-45-(-25))/10=-2\).

### Reverse 5Y (`reverse_5y`, \(d=-1\))

\[
SS=397-410=-13, \qquad X^{gross}=-13-5=-18\ \text{bp}
\]

\[
C=300+300+150+400+200+150=1500\ \text{USD},
\quad TC=1500/1000=1.5\ \text{bp},
\quad X^{net,-1}=(-1)(-18)-1.5=16.5\ \text{bp}
\]

The previous values are \(123\times-10\), \(123\times0\), -12.5, 2.5,
-7.5, -2.5, and \(2\times-5\) bp. Their sum is
\(-1230-10-10-10=-1260\), so \(\mu=-1260/252=-5\) bp. Squared deviations
again total \(123\times25+123\times25+2\times56.25+2\times6.25=6275\),
so \(\sigma=\sqrt{6275/251}=5\) bp and
\(z=(-18-(-5))/5=-2.6\).

## Availability and proxy matrix

## Fail-closed conditions

## Deliberately unavailable items

## MG2 manual recalculation checklist
