from __future__ import annotations

import csv
import re
import tempfile
import unittest
from pathlib import Path

from data_pipeline.contracts import (
    APPROVED_ERIS_SYMBOL_PATTERN,
    ERIS_SETTLEMENT_FILENAME_PATTERN,
    MIGRATION_RULES,
    SCHEMAS,
    SchemaValidationError,
    migration_rule_for,
    validate_csv,
)


REQUIRED_SCHEMA_IDS = {
    "historical_rates",
    "historical_futures_settlements",
    "contract_reference",
    "contract_risk",
    "daily_market",
    "paper_quotes",
    "paper_decisions",
    "paper_orders",
    "paper_fills",
    "backtest_decisions",
    "backtest_orders",
    "backtest_fills",
    "paper_positions",
    "backtest_daily",
    "backtest_trades",
    "backtest_positions",
    "backtest_summary",
    "run_manifest",
    "run_inputs",
}


class SchemaCatalogTests(unittest.TestCase):
    def test_catalog_freezes_complete_nonduplicated_contracts(self) -> None:
        self.assertEqual(set(SCHEMAS), REQUIRED_SCHEMA_IDS)
        for schema_id, contract in SCHEMAS.items():
            with self.subTest(schema_id=schema_id):
                self.assertEqual(contract.schema_id, schema_id)
                self.assertEqual(contract.version, "1.0.0")
                self.assertTrue(contract.path_pattern)
                self.assertTrue(contract.columns)
                names = [column.name for column in contract.columns]
                self.assertEqual(len(names), len(set(names)))
                self.assertTrue(all(column.scalar_type and column.unit for column in contract.columns))
                self.assertTrue(all(column.reason for column in contract.columns))
                self.assertTrue(all(column.source_or_derivation for column in contract.columns))
                self.assertTrue(all(column.consumers for column in contract.columns))
                self.assertTrue(all(column.reason != "named consumer input or audit output" for column in contract.columns))
                self.assertTrue(contract.unique_key)
                self.assertTrue(set(contract.unique_key) <= set(names))
                self.assertTrue(contract.ordering)
                self.assertTrue(set(contract.ordering) <= set(names))
                self.assertTrue(contract.missing_value_policy)
                self.assertTrue(contract.update_frequency)
                self.assertTrue(contract.retention)
                self.assertTrue(contract.consumers)
                self.assertTrue(contract.validation_rules)

    def test_paper_and_backtest_contracts_have_no_adapter_only_columns(self) -> None:
        paper_agent = next(column for column in SCHEMAS["paper_decisions"].columns if column.name == "agent_id")
        self.assertFalse(paper_agent.nullable)
        self.assertNotIn("agent_id", [column.name for column in SCHEMAS["backtest_decisions"].columns])
        self.assertIn("ibkr_order_id", [column.name for column in SCHEMAS["paper_orders"].columns])
        self.assertNotIn("ibkr_order_id", [column.name for column in SCHEMAS["backtest_orders"].columns])

    def test_representative_columns_have_specific_lineage(self) -> None:
        market = {column.name: column for column in SCHEMAS["daily_market"].columns}
        self.assertEqual(market["available_at_utc"].reason, "causal publication cutoff")
        self.assertEqual(market["available_at_utc"].source_or_derivation, "approved source metadata normalized by canonicalizer")
        self.assertEqual(market["available_at_utc"].consumers, ("strategy.signal_generation",))
        paper_order = {column.name: column for column in SCHEMAS["paper_orders"].columns}
        self.assertEqual(paper_order["ibkr_order_id"].reason, "paper broker reconciliation")
        self.assertEqual(paper_order["ibkr_order_id"].consumers, ("agents.shared",))

    def test_historical_contracts_route_only_approved_sources(self) -> None:
        rates = SCHEMAS["historical_rates"]
        settlements = SCHEMAS["historical_futures_settlements"]

        self.assertEqual(rates.path_pattern, "data/source/fred/rates/rates_YYYY.csv")
        self.assertEqual(rates.consumers[0], "data_pipeline.fred_source")
        self.assertEqual(
            settlements.path_pattern,
            "data/source/cme/futures/futures_settlements_YYYY.csv",
        )
        self.assertEqual(settlements.consumers[0], "data_pipeline.cme_source")

        canonical_destinations = [
            rule.destination
            for rule in MIGRATION_RULES
            if rule.rule_id != "r2_inventory"
        ]
        self.assertFalse(
            any("quantt" in destination.lower() for destination in canonical_destinations)
        )
        r2_inventory = next(rule for rule in MIGRATION_RULES if rule.rule_id == "r2_inventory")
        self.assertEqual(
            r2_inventory.destination,
            "r2_objects.csv (retained in place; excluded from canonical inputs)",
        )


class CsvValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write_rows(self, name: str, header: list[str], rows: list[list[str]]) -> Path:
        path = self.root / name
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)
        return path

    def test_daily_market_accepts_causal_long_rows(self) -> None:
        contract = SCHEMAS["daily_market"]
        header = [column.name for column in contract.columns]
        path = self.write_rows(
            "market.csv",
            header,
            [
                ["2026-07-30", "", "ERIS-YIT-202609", "99.25", "price_points", "2026-07-30T21:00:00Z", "2026-07-30T21:00:00Z", "ERIS", "exact", ""],
                ["2026-07-30", "US-CMT-2Y", "", "388.5", "basis_points", "2026-07-30T20:00:00Z", "2026-07-30T20:01:00Z", "UST", "exact", ""],
            ],
        )
        self.assertEqual(validate_csv(contract, path), 2)

    def test_rejects_wrong_header_missing_values_and_types(self) -> None:
        contract = SCHEMAS["historical_rates"]
        with self.subTest(case="header"):
            path = self.write_rows("header.csv", ["wrong"], [["x"]])
            with self.assertRaisesRegex(SchemaValidationError, "header"):
                validate_csv(contract, path)
        header = [column.name for column in contract.columns]
        with self.subTest(case="missing"):
            path = self.write_rows("missing.csv", header, [["2026-07-30", "", "CMT", "2Y", "388.5"]])
            with self.assertRaisesRegex(SchemaValidationError, "required"):
                validate_csv(contract, path)
        with self.subTest(case="type"):
            path = self.write_rows("type.csv", header, [["not-a-date", "UST", "CMT", "2Y", "abc"]])
            with self.assertRaisesRegex(SchemaValidationError, "invalid"):
                validate_csv(contract, path)

    def test_rejects_ragged_rows(self) -> None:
        contract = SCHEMAS["backtest_orders"]
        header = [column.name for column in contract.columns]
        for name, row in {
            "short": ["o", "d", "2026-07-30T20:00:00Z", "ERIS", "BUY", "1", "MKT", "DAY"],
            "long": ["o", "d", "2026-07-30T20:00:00Z", "ERIS", "BUY", "1", "MKT", "DAY", "planned", "extra"],
        }.items():
            with self.subTest(case=name):
                path = self.write_rows(f"{name}.csv", header, [row])
                with self.assertRaisesRegex(SchemaValidationError, "row width"):
                    validate_csv(contract, path)

    def test_rejects_noncanonical_date_and_integer_lexemes(self) -> None:
        rates = SCHEMAS["historical_rates"]
        path = self.write_rows(
            "compact-date.csv",
            [column.name for column in rates.columns],
            [["20260730", "UST", "CMT", "2Y", "388.5"]],
        )
        with self.assertRaisesRegex(SchemaValidationError, "invalid observation_date"):
            validate_csv(rates, path)
        orders = SCHEMAS["backtest_orders"]
        path = self.write_rows(
            "underscored-int.csv",
            [column.name for column in orders.columns],
            [["o", "d", "2026-07-30T20:00:00Z", "ERIS", "BUY", "1_0", "MKT", "DAY", "planned"]],
        )
        with self.assertRaisesRegex(SchemaValidationError, "invalid quantity"):
            validate_csv(orders, path)

    def test_rejects_duplicate_keys_and_unsorted_rows(self) -> None:
        contract = SCHEMAS["historical_rates"]
        header = [column.name for column in contract.columns]
        duplicate = ["2026-07-30", "UST", "CMT", "2Y", "388.5"]
        path = self.write_rows("duplicate.csv", header, [duplicate, duplicate])
        with self.assertRaisesRegex(SchemaValidationError, "duplicate key"):
            validate_csv(contract, path)
        path = self.write_rows(
            "unsorted.csv",
            header,
            [
                ["2026-07-31", "UST", "CMT", "2Y", "389"],
                ["2026-07-30", "UST", "CMT", "2Y", "388"],
            ],
        )
        with self.assertRaisesRegex(SchemaValidationError, "ordering"):
            validate_csv(contract, path)

    def test_rejects_ambiguous_identity_and_noncausal_availability(self) -> None:
        contract = SCHEMAS["daily_market"]
        header = [column.name for column in contract.columns]
        both = ["2026-07-30", "CMT", "ERIS", "1", "basis_points", "2026-07-30T20:00:00Z", "2026-07-30T20:01:00Z", "source", "exact", ""]
        path = self.write_rows("both.csv", header, [both])
        with self.assertRaisesRegex(SchemaValidationError, "exactly one"):
            validate_csv(contract, path)
        late_observation = ["2026-07-30", "CMT", "", "1", "basis_points", "2026-07-30T20:01:00Z", "2026-07-30T20:00:00Z", "source", "exact", ""]
        path = self.write_rows("causality.csv", header, [late_observation])
        with self.assertRaisesRegex(SchemaValidationError, "available_at_utc"):
            validate_csv(contract, path)

    def test_rejects_non_utc_and_crossed_or_nonpositive_quotes(self) -> None:
        contract = SCHEMAS["paper_quotes"]
        header = [column.name for column in contract.columns]
        cases = {
            "timezone": ["2026-07-30T16:00:00-04:00", "ERIS", "1", "2", "1", "1"],
            "crossed": ["2026-07-30T20:00:00Z", "ERIS", "2", "1", "1", "1"],
            "nonpositive": ["2026-07-30T20:00:00Z", "ERIS", "1", "2", "0", "1"],
        }
        for name, row in cases.items():
            with self.subTest(case=name):
                path = self.write_rows(f"{name}.csv", header, [row])
                with self.assertRaises(SchemaValidationError):
                    validate_csv(contract, path)

    def test_order_side_must_match_signed_quantity(self) -> None:
        contract = SCHEMAS["backtest_orders"]
        header = [column.name for column in contract.columns]
        valid_sell = ["o-1", "d-1", "2026-07-30T20:00:00Z", "ERIS", "SELL", "-2", "MKT", "DAY", "planned"]
        self.assertEqual(validate_csv(contract, self.write_rows("sell.csv", header, [valid_sell])), 1)
        for name, side, quantity in (("bad-side", "banana", "2"), ("bad-sign", "BUY", "-2"), ("zero", "SELL", "0")):
            row = [name, "d-1", "2026-07-30T20:00:00Z", "ERIS", side, quantity, "MKT", "DAY", "planned"]
            with self.subTest(case=name):
                with self.assertRaisesRegex(SchemaValidationError, "side and signed quantity"):
                    validate_csv(contract, self.write_rows(f"{name}.csv", header, [row]))

    def test_sign_state_and_direction_domains_are_closed(self) -> None:
        risk = SCHEMAS["contract_risk"]
        risk_header = [column.name for column in risk.columns]
        for sign in ("0", "2"):
            with self.subTest(sign=sign):
                path = self.write_rows(f"risk-{sign}.csv", risk_header, [["2026-07-30", "ERIS", "40", sign, "observed"]])
                with self.assertRaisesRegex(SchemaValidationError, "rate_sensitivity_sign"):
                    validate_csv(risk, path)
        decisions = SCHEMAS["backtest_decisions"]
        decision_header = [column.name for column in decisions.columns]
        row = ["d", "2026-07-30T20:00:00Z", "v1", "hash", "2Y", "2", "0", "0", "bad", "", ""]
        with self.assertRaisesRegex(SchemaValidationError, "state or direction"):
            validate_csv(decisions, self.write_rows("decision.csv", decision_header, [row]))

    def test_dv01_signal_pair_and_trade_direction_are_fail_closed(self) -> None:
        risk = SCHEMAS["contract_risk"]
        path = self.write_rows("negative-dv01.csv", [column.name for column in risk.columns], [["2026-07-30", "ERIS", "-40", "-1", "observed"]])
        with self.assertRaisesRegex(SchemaValidationError, "dv01_usd_per_bp must be positive"):
            validate_csv(risk, path)
        decisions = SCHEMAS["backtest_decisions"]
        header = [column.name for column in decisions.columns]
        for value, unit in (("2.1", ""), ("", "zscore")):
            row = ["d", "2026-07-30T20:00:00Z", "v1", "hash", "2Y", "0", "1", "1", "enter", value, unit]
            with self.assertRaisesRegex(SchemaValidationError, "signal_value and signal_unit"):
                validate_csv(decisions, self.write_rows(f"signal-{value or 'blank'}.csv", header, [row]))
        trades = SCHEMAS["backtest_trades"]
        header = [column.name for column in trades.columns]
        for direction in ("0", "2"):
            row = [f"t-{direction}", "d", "2Y", direction, "2026-07-30T20:00:00Z", "", "0", "0", "0"]
            with self.assertRaisesRegex(SchemaValidationError, "trade direction"):
                validate_csv(trades, self.write_rows(f"trade-{direction}.csv", header, [row]))


class MigrationCoverageTests(unittest.TestCase):
    def test_every_p20_artifact_matches_exactly_one_nonexecuting_rule(self) -> None:
        inventory = Path("docs/data/current-inventory.md").read_text(encoding="utf-8")
        paths = re.findall(r"^### `([^`]+)`$", inventory, re.MULTILINE)
        self.assertEqual(len(paths), 1487)
        allowed_actions = {
            "keep immutable source",
            "regenerate",
            "archive labelled legacy",
            "supersede after validation",
        }
        counts = {rule.rule_id: 0 for rule in MIGRATION_RULES}
        for path in paths:
            rule = migration_rule_for(path)
            counts[rule.rule_id] += 1
            self.assertIn(rule.action, allowed_actions)
            self.assertFalse(rule.performs_action)
            self.assertTrue(rule.destination)
            self.assertTrue(rule.row_expectation)
            self.assertTrue(rule.column_expectation)
            self.assertTrue(rule.recovery)
            self.assertTrue(rule.reconciliation)
        self.assertEqual(sum(counts.values()), 1487)
        self.assertEqual(counts["eris_vendor_cache"], 1474)
        self.assertEqual(sum(count for key, count in counts.items() if key != "eris_vendor_cache"), 13)
        eris = next(rule for rule in MIGRATION_RULES if rule.rule_id == "eris_vendor_cache")
        self.assertEqual(APPROVED_ERIS_SYMBOL_PATTERN, r"^(?:YIT|YIW)[HMUZ]\d{2}$")
        self.assertEqual(ERIS_SETTLEMENT_FILENAME_PATTERN, r"^Eris_Instruments_(\d{8})_Settles\.csv$")
        self.assertIn(APPROVED_ERIS_SYMBOL_PATTERN, eris.selection_predicate)
        self.assertIn(ERIS_SETTLEMENT_FILENAME_PATTERN, eris.selection_predicate)
        self.assertIn("EvaluationDate,Symbol", eris.reconciliation)

    def test_unknown_or_overlapping_artifact_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly one migration rule"):
            migration_rule_for("data/unknown.csv")


if __name__ == "__main__":
    unittest.main()
