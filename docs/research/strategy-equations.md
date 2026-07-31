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
| `CME-ERIS-FAQ` | [Eris SOFR Swap Futures FAQ](https://www.cmegroup.com/markets/interest-rates/files/eris-sofr-swap-futures-faq.pdf); [Eris SOFR Swap Futures overview](https://www.cmegroup.com/markets/interest-rates/files/eris-sofr-swap-futures-overview.pdf) | One contract is USD 100,000 notional. A long receives fixed and pays compounded SOFR. The price is indexed to 100, and one price point is USD 1,000. YIT has a 0.0025-point minimum increment worth USD 2.50; YIW has a 0.0100-point minimum increment worth USD 10.00. The 2Y prefix is YIT and the 5Y prefix is YIW. | Contract convention only; current contract terms, prices, and DV01 still require validated as-of inputs. |
| `CME-SPREAD-PRIMER` | [Trading Swap Spreads with Futures: a primer for Eris/Treasury swap spreads](https://www.cmegroup.com/articles/2024/trading-swap-spreads-with-futures-a-primer-for-eristreasury-swap-spreads.html) | Buy spread means buy Eris and sell Treasury. The ETU 2Y displayed ratio is 2:1; the EWV 5Y displayed ratio is 1:1. | Published ratios are sanity checks, not the sizing authority. |
| `CME-ETU-NOTICE` | [CME Globex notice, 2024-03-11](https://www.cmegroup.com/notices/electronic-trading/2024/03/20240311.html) | ETU is YIT versus ZT with leg quantity ratio 2:1. | Confirms the published product mapping and ratio only. |
| `CME-EWV-NOTICE` | [CME Globex notice, 2023-11-06](https://www.cmegroup.com/notices/electronic-trading/2023/11/20231106.html) | EWV is YIW versus ZF with leg quantity ratio 1:1. | Confirms the published product mapping and ratio only. |
| `CME-TREASURY-SPECS` | [Understand Treasuries contract specifications](https://www.cmegroup.com/education/courses/introduction-to-treasuries/understand-treasuries-contract-specifications.hideSubnav.educationIframe.html.html?hideAddThisExt=y&hideFooter=y&hideHeader=y&hideRightRail=y); [2-Year T-Note futures contract specs](https://www.cmegroup.com/markets/interest-rates/us-treasury/2-year-us-treasury-note.contractSpecs.html); [5-Year T-Note futures contract specs](https://www.cmegroup.com/markets/interest-rates/us-treasury/5-year-us-treasury-note.contractSpecs.html); [Treasury futures delivery process](https://www.cmegroup.com/content/dam/cmegroup/trading/interest-rates/files/us-treasury-futures-delivery-process.pdf) | ZT face amount/contract factor is USD 200,000/USD 2,000 per point; its futures minimum increment is 1/8 of 1/32, or 0.00390625 point and USD 7.8125. ZF is USD 100,000/USD 1,000 per point; its minimum increment is 1/4 of 1/32, or 0.0078125 point and USD 7.8125. Long prices fall when yields rise. Delivery and CTD conventions govern current DV01. | Current contract/CTD DV01 is required for executable sizing; contract amounts, ticks, and quoted multipliers alone are insufficient. |
| `UST-CMT` | [Treasury yield curve methodology](https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics/treasury-yield-curve-methodology); [Interest rates FAQ](https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics/interest-rates-frequently-asked-questions) | CMT is a Treasury par-yield-curve estimate, not a futures yield or futures price. | It may support a maturity-matched Treasury-rate input only when its timestamp and units satisfy the stated economic contract. |
| `NYFED-RATES` | [New York Fed reference rates](https://www.newyorkfed.org/markets/reference-rates) | SOFR, EFFR, and repo-family rates are distinct published reference rates; `EFFR-SOFR` remains a proxy for exact `L-repo`. | The exact maturity/collateral-consistent `L-repo` input is unavailable until validated; do not relabel the proxy as exact. |

The CME page showing `EAT` at 1:1 describes the YIA/YIT Eris-vs-Eris curve
spread, not the `ETU` YIT/ZT Treasury spread. It therefore does not override or
conflict with the ETU 2:1 notice. Published ratios remain sanity checks;
current contract/CTD DV01 remains authoritative for sizing.

## Source-quote normalization and tick units

Eris fixture prices are decimal index points. Their exact tick values follow
from the official minimum increments and the USD 1,000-per-point multiplier:

\[
tickUSD_{YIT}=0.0025(1000)=2.50,\qquad
tickUSD_{YIW}=0.0100(1000)=10.00.
\]

Treasury source quotes are normalized from whole points \(W\), whole 32nds
\(N_{32}\), and eighths of a 32nd \(N_8\) before P&L:

\[
P=W+\frac{N_{32}}{32}+\frac{N_8}{256},
\qquad 0\leq N_{32}<32,\quad 0\leq N_8<8.
\]

For ZT, one futures tick is 1/8 of 1/32, or 1/256 = 0.00390625 point;
at USD 2,000 per point it is USD 7.8125. For ZF, one tick is 1/4 of
1/32, or 2/256 = 0.0078125 point; at USD 1,000 per point it is also
USD 7.8125. These are outright futures conventions, not 2Y option or other-
tenor increments.

The traditional ZTH27 example converts `(102, 0, 0)` to 102.000000 and
`(101, 31, 4)` to 101.984375. Its -0.015625-point move is exactly -4 ZT
ticks. The reverse ZFH27 example converts `(108, 0, 0)` to 108.000000 and
`(108, 0, 4)` to 108.015625. Its +0.015625-point move is exactly +2 ZF
ticks. The YITH27 +0.0125-point move is +5 YIT ticks, and the YIWH27
-0.0100-point move is -1 YIW tick. A malformed fractional quote or a price
off its official tick grid blocks exact contract P&L rather than being rounded.

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
available at \(t\): a maximum 60 completed business dates, a minimum 40
completed business dates, and a frozen 20-business-day forecast horizon. With
\(n_t\) such observations,

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
does not change the 5-bp result. The 60/40 funding history is a consecutive
suffix ending at (t-1); a missing required business date breaks the suffix,
even when older observations exist. P10 demonstrates this dated selection
with an explicit synthetic Monday-Friday business-day calendar with no
holidays. The real production business-day/holiday calendar remains
`unavailable pending P11` validation and is never silently inferred from the
synthetic calendar.

Every selected funding record also carries `available_utc` no later than the
saved `decision_utc`. A record for a required date published after that cutoff
is absent at the decision and therefore breaks the suffix. Two eligible
records for one observation date are ambiguous and make the history
unavailable. Current-date and future-date records are never selected. A later
revision whose publication is after the saved decision is excluded, so it
cannot revise the saved historical funding estimate.

## Decision clock and movement trigger

For a decision using CMS, CMT, floating, and repo inputs, every record must
have exactly the same observation date and exact field identity. The
`decision_utc` is the maximum of the required same-observation-date publication
timestamps. No guessed fixed clock, forward fill, or intraday interpolation is
permitted. A missing field, duplicate field, prior-date substitute, or record
published after the saved decision makes the affected input set unavailable;
future publications do not revise a saved historical decision.

For a two-endpoint movement, the saved clocks must satisfy
`previous_decision_utc < current_decision_utc`. Equal or reversed decision
timestamps make the movement unavailable even when both endpoint snapshots
are otherwise complete.

The Agent 2 movement input is the maturity-matched fixed swap-spread change
over one adjacent synchronized completed-business-day interval:

\[
\Delta SS_{m,t}=SS_{m,t}-SS_{m,t-1}.
\]

Both endpoint snapshots require exactly the same CMS, CMT, floating, and repo
field identities, the same maturity and basis-point unit, and publication no
later than their respective saved decision timestamps. The prior observation
date must be the completed business date immediately preceding the current
observation date under the supplied calendar. A missing endpoint, duplicate
field, gap, late endpoint publication, wrong series, or wrong maturity makes
the movement unavailable; no prior-date value is forward filled. Records from
later dates are excluded and cannot revise the saved movement. The movement
does not use an Eris/Treasury price difference. Its frozen classification is:

\[
movement(\Delta r)=
\begin{cases}
+1,&\Delta r\geq5.00\ \text{bp}\\
-1,&\Delta r\leq-5.00\ \text{bp}\\
0,&\text{otherwise.}
\end{cases}
\]

Thus the six fixture results are literal boundary checks: 4.99 maps to 0,
5.00 and 5.01 map to +1, -4.99 maps to 0, and -5.00 and -5.01 map to
-1. The threshold is inclusive in both directions. A decision still requires
the causal funding and z-score histories and all maturity-matched inputs to be
ready; movement alone cannot authorize an entry.

## Causal z-score and state rules

The causal standardized gross opportunity is:

\[
z_{m,t}=\frac{X^{gross}_{m,t}-\mu_{m,t^-}}{\sigma_{m,t^-}}
\]

where \(\mu_{m,t^-}\) and \(\sigma_{m,t^-}\) are respectively the mean and
sample standard deviation of exactly the 252 completed business dates
preceding \(t\), excluding \(X^{gross}(t)\):

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

The 252 inputs are selected by observation date and `available_utc`, not by
list length alone. They must be the exact consecutive completed business dates
immediately preceding \(t\), and every record must be published no later than
the saved `decision_utc`. A gap, an eligible duplicate date, or a late required
record makes the history unavailable. Current/future rows are excluded, and a
later revision published after the saved decision cannot change the saved
history or z-score.

The position state is \(p\in\{-1,0,+1\}\), where +1 is traditional and -1
is reverse. Entry eligibility is directional and strictly positive:

\[
E^+=\left[z\geq2.0\right]\land\left[X^{net,+1}>0\right],\qquad
E^-=\left[z\leq-2.0\right]\land\left[X^{net,-1}>0\right].
\]

At a flat position, \(E^+\) enters traditional and \(E^-\) enters reverse.
At an existing position, an eligible opposite entry is a reversal with two
ordered actions: exit the existing side, then enter the new side. Otherwise an
existing side exits when \(|z|\leq0.5\), inclusive, or when that side's net
opportunity is nonpositive. It persists outside those conditions.

The explicit entry checks are: 1.9999 stays flat, while 2.0 and 2.0001 enter
traditional; -1.9999 stays flat, while -2.0 and -2.0001 enter reverse. From a
traditional position, \(|z|=0.4999\) and 0.5 exit, while 0.5001 persists when
traditional net opportunity remains positive.

| Fixture state | Input summary | Result |
|---|---|---|
| `traditional_entry_at_boundary` | flat, \(z=2.0\), traditional net = 0.0001 bp | +1; `enter_traditional` |
| `traditional_persistence` | +1, \(z=1.0\), traditional net = 1 bp | +1; no action |
| `traditional_hysteretic_exit_at_boundary` | +1, \(z=0.5\) | 0; `exit_traditional` |
| `reverse_entry_at_boundary` | flat, \(z=-2.0\), reverse net = 0.0001 bp | -1; `enter_reverse` |
| `traditional_to_reverse_reversal` | +1, \(z=-2.0\), reverse net = 2 bp | -1; `exit_traditional`, then `enter_reverse` |
| `risk_flatten_overrides_entry` | +1 with an otherwise eligible reverse entry | 0; `risk_flatten` only |
| `missing_data_flattens` | -1 with data not ready | 0; `data_flatten` only |
| `nonpositive_net_exits` | -1, reverse net = 0 bp | 0; `exit_reverse` |

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

One long Eris SOFR Swap Futures contract receives fixed and pays compounded
SOFR. Long Treasury futures prices fall when yields rise. Consequently, the
economic direction establishes the quantity signs before DV01 sizing:

| Direction | `d` | Eris quantity sign | Swap exposure | Treasury quantity sign |
|---|---:|---:|---|---:|
| Traditional | `+1` | positive | receive fixed/pay compounded SOFR | negative |
| Reverse | `-1` | negative | pay fixed/receive compounded SOFR | positive |

The side mapping is fixed, but published 2:1 ETU and 1:1 EWV ratios are only
sanity checks. The independently selected integer basket uses current,
validated contract/CTD DV01 magnitudes; unavailable DV01 blocks execution.

## Integer DV01 hedge

The selector accepts positive DV01 magnitudes \(D_S\) and \(D_T\). Because a
long Eris or Treasury futures contract loses value for a one-basis-point rate
increase, each signed long-contract exposure is explicitly

\[
\delta_S=-D_S,\qquad\delta_T=-D_T.
\]

For positive target \(D^*\), first apply the rule `half ties away from zero` to
the positive swap magnitude and apply the economic direction:

\[
n_S=d\,round_{half\ away}(D^*/D_S).
\]

A zero rounded swap leg blocks the basket. Otherwise solve the continuous
Treasury quantity

\[
n_T^*=-\frac{n_S\delta_S}{\delta_T},
\]

test only \(\lfloor n_T^*\rfloor\) and the adjacent ceiling, and minimize in
order: absolute net DV01, gross DV01, then integer Treasury quantity. Net and
residual are

\[
DV01^{net}=n_S\delta_S+n_T\delta_T,\qquad
\rho=|DV01^{net}|/D^*.
\]

Both legs must be nonzero and \(\rho\leq0.05\), inclusive. Invalid direction,
nonpositive target, or nonpositive leg DV01 returns a blocked zero-leg result.

The fixture calculations are:

- `traditional_exact_5_percent`: \(n_S=round(1000/100)=10\),
  \(n_T^*=-1.0526\ldots\), and -1 beats -2. Net is
  \(10(-100)+(-1)(-950)=-50\), so \(\rho=50/1000=0.05\): allowed.
- `reverse_exact_5_percent`: \(n_S=-10\), \(n_T^*=1.0526\ldots\), and
  \(n_T=1\). Net is \((-10)(-100)+1(-950)=50\), so \(\rho=0.05\): allowed.
- `traditional_5_01_percent_block`: \(n_S=10\), \(n_T=-1\), net is
  \(-1000+949.9=-50.1\), and \(\rho=50.1/1000=0.0501\): blocked.
- `reverse_4_99_percent_allow`: \(n_S=-10\), \(n_T=1\), net is
  \(1000-950.1=49.9\), and \(\rho=49.9/1000=0.0499\): allowed.
- `tie_chooses_lower_gross`: \(n_S=round(300/100)=3\) and
  \(n_T^*=-1.5\). Candidates -2 and -1 both leave 100 USD/bp absolute net;
  their gross exposures are 700 and 500 USD/bp, so -1 wins. Net is -100 and
  \(\rho=100/300=0.33333333333333333333333333333333333333333333333333\):
  blocked.
- `half_swap_rounds_away`: 250/100 = 2.5 rounds to \(n_S=3\), not 2;
  \(n_T^*=-2\), net is \(3(-100)+(-2)(-150)=0\), and \(\rho=0\): allowed.
- `zero_swap_blocks`: 40/100 = 0.4 rounds to zero, so both returned
  quantities, net, and residual are zero and the basket is blocked.

## Contract P&L, reversal, roll, and flattening

Each contract is marked only over its own price history under the
`same-contract` rule:

\[
PnL_i=q_iM_i(P_i^{end}-P_i^{start})-C_i.
\]

The signed quantity carries the long/short direction. Costs are charged to the
action that incurs them. The fixture arithmetic is:

Each non-roll leg carries one immutable full expiry identity at both price
endpoints. A root-only or continuous-series label does not prove
same-contract P&L and is unavailable for an exact example. The synthetic
examples use YITH27 with ZTH27 and YIWH27 with ZFH27; their start and end
instrument IDs are equal before the price change is calculated. Roll examples
instead require explicitly different old and new full identities with the
timestamp boundary below.

The roll oracle treats each contract `symbol` as its immutable identity. Both
old and new symbols must match the full-expiry form
`(?:YIT|YIW|ZT|ZF)[FGHJKMNQUVXZ]\d{2}` and must differ. A root-only, malformed,
missing, or identical old/new identity makes roll P&L unavailable.

The roll clock is explicit and gap-free:

\[
t^{old,end}=t^{close}=t^{roll}=t^{open}=t^{new,start},
\qquad t^{old,start}<t^{roll}<t^{new,end}.
\]

The quantity held before the roll earns only its own-contract price change
through the close/roll timestamp. The new quantity earns only its
own-contract price change after the open/roll timestamp. Any overlap, gap, or
misordered close/open timestamp makes the roll calculation unavailable. At a
saved as-of timestamp, only intervals whose end timestamp has arrived may
contribute; a later new-contract price cannot change the old-contract P&L
saved at the roll. Close and open costs and both turnover legs are charged at
the roll boundary.

- `traditional_same_contract`: YITH27 contributes
  \(2(1000)(100.1125-100.1000)=25\) USD. ZTH27 contributes:
  \((-1)(2000)(101.984375-102.000000)-6.25=25\) USD. Total: 50 USD.
  This checks both the positive Eris/negative Treasury signs and the exact
  1,000/2,000 USD-per-point multipliers.
- `reverse_same_contract`: YIWH27 contributes
  \((-1)(1000)(99.4900-99.5000)=10\) USD. ZFH27 contributes
  \(1(1000)(108.015625-108.000000)-5.625=10\) USD. Total: 20 USD.
- `eris_roll`: old YITH27 P&L is
  \(2(1000)(100.1100-100.1000)=20\) USD and new YITM27 P&L is
  \(2(1000)(99.9050-99.9000)=10\) USD. Same-contract P&L is 30 USD; close
  plus open cost is \(3+4=7\) USD; net is 23 USD; turnover is
  \(|2|+|2|=4\) contracts. The cross-contract change
  \(99.9000-100.1100=-0.2100\), which would create -420 USD at the old
  quantity and multiplier, is not a return and is never used. The old interval
  ends at `2027-03-10T20:31:00Z`, exactly when the old leg closes and the new
  leg opens. At that timestamp old mark P&L is 20 USD, the future new mark is
  zero, roll costs are 7 USD, and net is 13 USD. The new interval ends at
  `2027-03-11T20:31:00Z`; only then does its 10 USD mark enter the 23 USD final
  net.
- `reversal_cost`: the exit and opposite entry are distinct actions. Cost is
  \(275+325=600\) USD and turnover is \(|3|+|2|=5\) contracts; neither the
  cost nor the turnover is netted across the reversal.

Flattening is fail-closed. `risk_flatten` has first precedence and produces a
single risk-flatten action for a nonzero position, even if an opposite entry
would otherwise qualify. If there is no risk flatten but required data is not
ready, a nonzero position produces one `data_flatten` action. A flat position
produces no redundant flatten action. Risk or data flatten never includes an
entry action in the same transition.

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

| Field/output | Required classification in P10 |
|---|---|
| maturity-matched CMS 2Y/5Y history | unavailable pending P11 source validation |
| Treasury CMT 2Y/5Y | exact only when official same-date rate and publication metadata pass |
| exact floating reference `L` | unavailable until approved mapping |
| maturity/collateral-consistent repo | unavailable pending P11 |
| production business-day/holiday calendar | unavailable pending P11 |
| `EFFR-SOFR` | proxy |
| funding estimator parameters | assumed/frozen research parameters |
| example execution costs | assumed synthetic |
| current Eris DV01 | exact only from validated current contract metadata |
| current Treasury futures DV01 | exact only from validated CTD and conversion factor |
| CME displayed 2:1/1:1 ratios | exact published facts, sanity checks only |
| economic example outputs | derived synthetic |
| 2Y/5Y executable basket | derived only when all contract inputs are exact |
| 10Y/30Y executable basket | unavailable |
| intraday trigger | unavailable |
| forward funding curve | unavailable pending P11 |
| complete four-maturity strategy result | unavailable |

## Fail-closed conditions

Input classifications are mutually exclusive. Any proxy input lineage
propagates to every derived output, which remains derived-with-proxy-lineage
and cannot be presented as an exact result or complete strategy output.

Non-finite, missing, stale, wrong-unit, wrong-maturity, or late economic input
blocks the affected output. Every contract price, official price multiplier,
swap DV01, and Treasury DV01 input must be finite and strictly positive; a
missing or invalid value blocks the applicable P&L or basket calculation. An
unresolved contract sign or official multiplier blocks P&L or execution as
applicable. A one-leg basket or a DV01 residual above 5% blocks execution. A
missing executable leg is represented only as the blocked zero-leg basket; it
is never emitted as a one-leg position. Risk flattening overrides entry or
reversal and produces no entry action in the same transition.

## Deliberately unavailable items

P10 does not claim a complete executable strategy. Exact CMS history, exact
floating-reference mapping, collateral-consistent repo, the production
business-day calendar, forward funding curve, 10Y/30Y executable baskets, and
an intraday trigger remain unavailable as shown in the matrix. The 2Y/5Y
equations and synthetic examples are the bounded contract pending MG2 and P11
validation; they do not fill any unavailable input with a proxy.

## MG2 manual recalculation checklist
