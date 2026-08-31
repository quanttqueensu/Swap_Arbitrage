from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from data_pipeline.live_data_pipeline.auto_refresh import (
    AutoRefreshError,
    Agent1DataRefresher,
    build_baseline_frame,
    build_reference_frame,
    load_fred_yield_history,
    load_latest_eris_reference_frame,
)
from strategy.live_signal import LIVE_SIGNAL_STRATEGY_VERSION


class AutoRefreshTests(unittest.TestCase):
    @staticmethod
    def source_frame(evaluation_date: str = "08/28/2026") -> pd.DataFrame:
        rows = []
        for symbol, root, coupon, pv01, dv01 in (
            ("YITM26", "YIT", "4.25", "19.5", "19.2"),
            ("YIWM26", "YIW", "4.50", "44.0", "43.2"),
        ):
            rows.append(
                {
                    "Symbol": symbol,
                    "ExchangeSymbol (EX005)": root,
                    "Coupon (%)": coupon,
                    "PastFxdFltPmts (B)": "0.125",
                    "ErisPAI (C)": "0.025",
                    "PV01": pv01,
                    "DV01": dv01,
                    "EffectiveDate": "06/17/2026",
                    "MaturityDate": "06/17/2031",
                    "EvaluationDate": evaluation_date,
                    "EffectiveYearMonth": "202606",
                }
            )
        return pd.DataFrame(rows)

    def test_reference_rows_match_exact_ibkr_local_symbols(self) -> None:
        bindings = {
            "2Y:swap": SimpleNamespace(
                con_id=101, symbol="YIT", local_symbol="YITM26", risk_id="ERIS-YIT-202606"
            ),
            "5Y:swap": SimpleNamespace(
                con_id=202, symbol="YIW", local_symbol="YIWM26", risk_id="ERIS-YIW-202606"
            ),
        }
        observed = datetime(2026, 8, 28, 21, tzinfo=timezone.utc)

        output = build_reference_frame(
            self.source_frame(), bindings, observed_at=observed
        )

        yit = output[output["symbol"].eq("YIT")].iloc[0]
        self.assertEqual(yit["contract_id"], "101")
        self.assertEqual(yit["local_symbol"], "YITM26")
        self.assertEqual(Decimal(yit["fixed_rate_decimal"]), Decimal("0.0425"))
        self.assertEqual(Decimal(yit["pv01_usd_per_bp"]), Decimal("19.5"))
        self.assertEqual(Decimal(yit["dv01_usd_per_bp"]), Decimal("19.2"))

    def test_latest_reference_scans_back_over_weekend(self) -> None:
        def downloader(url: str) -> pd.DataFrame:
            if "20260828_Settles" in url:
                return self.source_frame()
            raise AutoRefreshError("missing")

        frame, observed = load_latest_eris_reference_frame(
            datetime(2026, 8, 30, 14, tzinfo=timezone.utc),
            downloader=downloader,
        )

        self.assertEqual(observed.isoformat(), "2026-08-28")
        self.assertEqual(len(frame), 2)

    def test_reference_can_match_ibkr_year_format_by_effective_vintage(self) -> None:
        bindings = {
            "2Y:swap": SimpleNamespace(
                con_id=101, symbol="YIT", local_symbol="YITM6", risk_id="ERIS-YIT-202606"
            ),
            "5Y:swap": SimpleNamespace(
                con_id=202, symbol="YIW", local_symbol="YIWM6", risk_id="ERIS-YIW-202606"
            ),
        }

        output = build_reference_frame(
            self.source_frame(),
            bindings,
            observed_at=datetime(2026, 8, 28, 21, tzinfo=timezone.utc),
        )

        self.assertEqual(output[output["symbol"].eq("YIT")].iloc[0]["local_symbol"], "YITM26")

    def test_baseline_is_exact_trailing_252_aligned_spreads(self) -> None:
        start = datetime(2025, 1, 1)
        dates = [start + timedelta(days=i) for i in range(320)]
        eris = pd.DataFrame(
            {
                "date": dates,
                "eris_swap_2y_equivalent_par_rate_bps": range(400, 720),
                "eris_swap_5y_equivalent_par_rate_bps": range(500, 820),
            }
        )
        yields = {
            "2Y": pd.DataFrame({"date": dates, "yield_percent": [4] * 320}),
            "5Y": pd.DataFrame({"date": dates, "yield_percent": [5] * 320}),
        }

        output = build_baseline_frame(eris, yields)

        self.assertEqual(len(output), 504)
        self.assertEqual((output["maturity"] == "2Y").sum(), 252)
        first_2y = output[output["maturity"].eq("2Y")].iloc[0]
        self.assertEqual(first_2y["spread_bps"], 68)
        self.assertEqual(first_2y["fred_series"], "DGS2")
        self.assertEqual(first_2y["treasury_rate_bps"], 400)
        self.assertTrue(
            output["strategy_version"].eq(LIVE_SIGNAL_STRATEGY_VERSION).all()
        )

    def test_fred_loader_parses_daily_cmt_and_drops_missing_values(self) -> None:
        source = pd.DataFrame(
            {
                "observation_date": pd.date_range("2025-01-01", periods=260),
                "DGS2": ["."] * 8 + ["4.25"] * 252,
            }
        )
        output = load_fred_yield_history(
            "DGS2",
            datetime(2026, 8, 30, 14, tzinfo=timezone.utc),
            downloader=lambda url: source,
        )
        self.assertEqual(len(output), 252)
        self.assertEqual(output.iloc[-1]["yield_percent"], 4.25)

    def test_refresher_generates_every_runtime_data_file(self) -> None:
        start = datetime(2025, 1, 1)
        dates = [start + timedelta(days=i) for i in range(320)]
        eris_history = pd.DataFrame(
            {
                "date": dates,
                "eris_swap_2y_equivalent_par_rate_bps": range(400, 720),
                "eris_swap_5y_equivalent_par_rate_bps": range(500, 820),
            }
        )
        bindings = {
            "2Y:swap": SimpleNamespace(
                con_id=101, symbol="YIT", local_symbol="YITM26", risk_id="ERIS-YIT-202606"
            ),
            "2Y:treasury": SimpleNamespace(
                con_id=102, symbol="ZT", local_symbol="ZTU26", risk_id="YAHOO-CONTINUOUS-ZT"
            ),
            "5Y:swap": SimpleNamespace(
                con_id=201, symbol="YIW", local_symbol="YIWM26", risk_id="ERIS-YIW-202606"
            ),
            "5Y:treasury": SimpleNamespace(
                con_id=202, symbol="ZF", local_symbol="ZFU26", risk_id="YAHOO-CONTINUOUS-ZF"
            ),
        }
        config = SimpleNamespace(
            min_days_to_expiry=14,
            max_2y_swap_contracts=100,
            max_2y_treasury_contracts=100,
            max_5y_swap_contracts=100,
            max_5y_treasury_contracts=100,
        )

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            refresher = Agent1DataRefresher(
                ib=object(),
                agent_config=config,
                baseline_path=root / "baseline.csv",
                reference_path=root / "reference.csv",
                contract_risk_path=root / "risk.csv",
                binding_resolver=lambda *args, **kwargs: bindings,
                current_reference_loader=lambda now: (
                    self.source_frame(),
                    datetime(2026, 8, 28).date(),
                ),
                eris_history_loader=lambda *args, **kwargs: eris_history,
                yield_history_loader=lambda symbol, now: pd.DataFrame(
                    {
                        "date": dates,
                        "yield_percent": [4 if symbol == "DGS2" else 5] * len(dates),
                    }
                ),
            )

            result = refresher.refresh(
                datetime(2026, 8, 30, 14, tzinfo=timezone.utc)
            )

            self.assertEqual(result.baseline_rows, {"2Y": 252, "5Y": 252})
            self.assertEqual(
                result.risk_inputs["2Y"].swap_dv01_per_contract,
                Decimal("19.2"),
            )
            self.assertEqual(
                result.risk_inputs["2Y"].treasury_dv01_per_contract,
                Decimal("38.40"),
            )
            self.assertEqual(len(pd.read_csv(root / "reference.csv")), 2)
            self.assertEqual(len(pd.read_csv(root / "risk.csv")), 4)
            self.assertEqual(len(pd.read_csv(root / "baseline.csv")), 504)


if __name__ == "__main__":
    unittest.main()
