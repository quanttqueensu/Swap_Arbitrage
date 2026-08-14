# QUANTT Swap Arb Hypothesis

# Swap Spread Arbitrage Hypothesis

This strategy is based on the traditional swap spread arbitrage framework loosely outlined in Literature Review 2. The core idea is to identify when the spread between swap rates and Treasury rates is unusually attractive relative to the expected cost of funding. The trade attempts to profit from the difference between the fixed swap spread received and the floating funding spread paid, while controlling for interest rate risk, funding risk, liquidity risk, and transaction costs.

In the traditional trade, the strategy receives fixed and pays floating in a par interest rate swap, shorts a maturity-matched Treasury bond, and invests the short-sale proceeds at the repo rate.

The combined position receives the fixed swap spread:

SS = CMS - CMT

The combined position pays the floating funding spread:

S_t = L_t - r_t

Where:

CMS = constant maturity swap rate

CMT = constant maturity Treasury rate

L_t = floating reference rate

r_t = repo rate

The trade is expected to be profitable when the fixed swap spread received is greater than the expected average floating funding spread paid over the life of the trade:

CMS - CMT > E[L_t - r_t]

However, the strategy should not enter every trade where the fixed spread is simply above the floating funding spread. The opportunity must be large enough to compensate for transaction costs and the possibility that the spread remains dislocated for an extended period.

The main signal is the excess spread:

Excess Spread = (CMS - CMT) - E[L_t - r_t]

A positive excess spread means the strategy expects to receive more from the fixed swap spread than it pays through the floating funding spread. A negative excess spread means the reverse trade may be more attractive.

To make the signal more adaptive, the model standardizes the excess spread using a rolling z-score:

z = (Current Excess Spread - Rolling Mean Excess Spread) / Rolling Standard Deviation of Excess Spread

This allows the strategy to adjust to the current volatility environment. A 10 basis point spread may be meaningful when markets are calm, but less meaningful when spreads are highly volatile. The z-score helps identify whether the current excess spread is unusually large relative to recent market conditions.

The entry rule should have two parts. First, the excess spread must be economically attractive after estimated transaction costs. Second, the z-score must be extreme enough to suggest a meaningful dislocation.

For example, the model may enter the traditional trade when:

Excess Spread > Transaction Cost Buffer

and:

z > +2

This would indicate that the fixed swap spread is large relative to the expected floating funding spread and unusually high compared with recent

 history. In this case, the model would receive fixed on the swap, pay floating, short the maturity-matched Treasury, and invest the proceeds at repo.

If the opposite condition holds, the model can enter the reverse trade. This would involve paying fixed on the swap, receiving floating, going long the Treasury bond, and financing the position. The reverse trade would be used when the fixed swap spread is unusually low relative to the expected floating funding spread.

The strategy should also compare opportunities across maturities. Instead of focusing on only one maturity, the model should calculate excess spreads for the 2-year, 5-year, 10-year, and 30-year points. It can then rank each maturity by attractiveness after adjusting for volatility, liquidity, and transaction costs. This makes the strategy more capital efficient because it directs risk toward the strongest relative-value opportunities instead of treating every signal equally.

Position sizing should be based on DV01 rather than notional value. DV01 measures how much the position gains or loses for a one basis point move in rates. Since the goal is to trade the swap spread rather than make a directional interest rate bet, the swap leg and Treasury leg should be matched by DV01. This helps isolate the spread exposure and reduces the risk that the position becomes an outright bet on interest rates.

The strategy should also use volatility targeting. When swap spread volatility is low, the model can take larger positions. When volatility rises, the model should reduce position size. This prevents the strategy from taking excessive risk during stressed markets, when swap spreads may become unstable and convergence may be less reliable.

The strategy is not risk-free. According to reviews based on literature review 3, It can lose money if funding conditions worsen, if repo rates move unexpectedly, if the floating funding spread becomes unstable, or if swap spreads remain dislocated for a long period.