from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class NaiveAssumptions:
    bid_ask_half_spread_points: Decimal
    commission_usd_per_contract: Decimal
    slippage_points: Decimal
    financing_usd_per_contract_day: Decimal
    roll_usd_per_contract: Decimal

    def __post_init__(self) -> None:
        values = (
            self.bid_ask_half_spread_points,
            self.commission_usd_per_contract,
            self.slippage_points,
            self.financing_usd_per_contract_day,
            self.roll_usd_per_contract,
        )
        if any(type(value) is not Decimal or not value.is_finite() or value < 0 for value in values):
            raise ValueError("naive assumptions must be finite nonnegative Decimals")


NAIVE_ASSUMPTIONS = NaiveAssumptions(
    bid_ask_half_spread_points=Decimal("0.01"),
    commission_usd_per_contract=Decimal("1"),
    slippage_points=Decimal("0.005"),
    financing_usd_per_contract_day=Decimal("0.10"),
    roll_usd_per_contract=Decimal("1"),
)
