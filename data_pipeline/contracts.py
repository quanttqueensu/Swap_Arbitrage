from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


SCHEMA_VERSION = "1.0.0"
APPROVED_ERIS_SYMBOL_PATTERN = r"^(?:YIT|YIW)[HMUZ]\d{2}$"
ERIS_SETTLEMENT_FILENAME_PATTERN = r"^Eris_Instruments_(\d{8})_Settles\.csv$"


class SchemaValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ColumnContract:
    name: str
    scalar_type: str
    unit: str
    nullable: bool = False
    reason: str = ""
    source_or_derivation: str = ""
    consumers: tuple[str, ...] = ()


@dataclass(frozen=True)
class CsvContract:
    schema_id: str
    version: str
    path_pattern: str
    columns: tuple[ColumnContract, ...]
    unique_key: tuple[str, ...]
    ordering: tuple[str, ...]
    missing_value_policy: str
    update_frequency: str
    retention: str
    consumers: tuple[str, ...]
    validation_rules: tuple[str, ...]


@dataclass(frozen=True)
class MigrationRule:
    rule_id: str
    pattern: str
    action: str
    destination: str
    row_expectation: str
    column_expectation: str
    recovery: str
    selection_predicate: str = "all source rows"
    reconciliation: str = "exact source-to-output key and value reconciliation"
    performs_action: bool = False

    def matches(self, path: str) -> bool:
        return re.fullmatch(self.pattern, path) is not None


def _column(
    spec: str,
    schema_id: str,
    key: tuple[str, ...],
    consumers: tuple[str, ...],
) -> ColumnContract:
    name, scalar_type, unit, nullable = (spec.split("|") + [""])[:4]
    origin = {
        "historical_rates": "approved rate source adapter",
        "historical_futures_settlements": "approved settlement source adapter",
        "contract_reference": "approved contract metadata canonicalizer",
        "contract_risk": "approved DV01/risk metadata canonicalizer",
        "daily_market": "approved source metadata normalized by canonicalizer",
        "paper_quotes": "IBKR paper quote adapter",
        "paper_decisions": "paper policy/strategy decision",
        "paper_orders": "IBKR paper order adapter",
        "paper_fills": "IBKR paper fill callback",
        "paper_positions": "IBKR paper position reconciliation",
        "backtest_decisions": "shared strategy decision during replay",
        "backtest_orders": "backtest order-intent adapter",
        "backtest_fills": "backtest fill simulator",
    }.get(schema_id, "backtest accounting or run manifest derivation")
    if name == "available_at_utc":
        reason = "causal publication cutoff"
    elif name == "ibkr_order_id":
        reason = "paper broker reconciliation"
    elif name in key:
        reason = "row identity and deterministic ordering"
    elif name.endswith(("_utc", "_date", "_time")):
        reason = "causal ordering or interval boundary"
    elif name in {"source", "classification", "proxy_label", "dv01_method"}:
        reason = "provenance and interpretation audit"
    elif name in {"status", "reason_code", "config_hash", "code_commit", "schema_version"}:
        reason = "reproducibility and audit attribution"
    else:
        reason = f"{name} value consumed by {consumers[0]}"
    column_consumers = ("strategy.signal_generation",) if schema_id == "daily_market" and name == "available_at_utc" else (consumers[0],)
    return ColumnContract(name, scalar_type, unit, nullable == "nullable", reason, origin, column_consumers)


def _schema(
    schema_id: str,
    path_pattern: str,
    columns: tuple[str, ...],
    key: tuple[str, ...],
    ordering: tuple[str, ...],
    frequency: str,
    retention: str,
    consumers: tuple[str, ...],
    rules: tuple[str, ...] = (),
) -> CsvContract:
    return CsvContract(
        schema_id=schema_id,
        version=SCHEMA_VERSION,
        path_pattern=path_pattern,
        columns=tuple(_column(spec, schema_id, key, consumers) for spec in columns),
        unique_key=key,
        ordering=ordering,
        missing_value_policy="empty fields allowed only where the column contract is nullable",
        update_frequency=frequency,
        retention=retention,
        consumers=consumers,
        validation_rules=("exact_header", "types", "required", "unique_key", "ordering") + rules,
    )


RATE_SOURCE_PATH = "data/source/rates/rates_YYYY.csv"
FUTURES_SOURCE_PATH = "data/source/futures/futures_settlements_YYYY.csv"


SCHEMAS = {
    "historical_rates": _schema(
        "historical_rates", RATE_SOURCE_PATH,
        ("observation_date|date|date", "source|string|source_id", "series_id|string|series_id", "maturity|string|maturity", "rate_bps|decimal|basis_points"),
        ("observation_date", "source", "series_id", "maturity"),
        ("observation_date", "source", "series_id", "maturity"), "daily by year", "immutable source capture",
        ("data_pipeline.rates_source", "data_pipeline.canonicalize"),
    ),
    "historical_futures_settlements": _schema(
        "historical_futures_settlements", FUTURES_SOURCE_PATH,
        ("observation_date|date|date", "source|string|source_id", "instrument_id|string|instrument_id", "settlement_price|decimal|price_points", "dv01_usd_per_bp|decimal|usd_per_bp|nullable"),
        ("observation_date", "source", "instrument_id"), ("observation_date", "source", "instrument_id"), "daily by year", "immutable source capture",
        ("data_pipeline.futures_source", "data_pipeline.canonicalize"), ("positive_dv01_if_present",),
    ),
    "contract_reference": _schema(
        "contract_reference", "data/canonical/reference/contracts.csv",
        ("instrument_id|string|instrument_id", "source|string|source_id", "asset_class|string|asset_class", "root|string|instrument_root", "contract_month|string|year_month", "maturity|string|maturity", "currency|string|currency", "exchange|string|exchange", "price_multiplier|decimal|usd_per_price_point", "tick_size|decimal|price_points", "valid_from|date|date", "valid_to|date|date"),
        ("instrument_id", "valid_from"), ("instrument_id", "valid_from"), "on approved reference change", "retain every validity interval",
        ("data_pipeline.canonicalize", "strategy.position_sizing", "backtesting.engine", "agents.shared"),
    ),
    "contract_risk": _schema(
        "contract_risk", "data/canonical/reference/contract_risk_YYYY.csv",
        ("observation_date|date|date", "instrument_id|string|instrument_id", "dv01_usd_per_bp|decimal|usd_per_bp", "rate_sensitivity_sign|integer|sign", "dv01_method|string|method"),
        ("observation_date", "instrument_id"), ("observation_date", "instrument_id"), "daily by year", "retain with input manifests",
        ("strategy.position_sizing", "strategy.risk_signals", "backtesting.engine", "agents.shared"), ("rate_sensitivity_sign_domain", "positive_dv01_if_present"),
    ),
    "daily_market": _schema(
        "daily_market", "data/canonical/market/daily_market_YYYY.csv",
        ("observation_date|date|date", "series_id|string|series_id|nullable", "instrument_id|string|instrument_id|nullable", "value|decimal|declared_by_value_unit", "value_unit|string|unit", "source_observation_time_utc|datetime_utc|utc", "available_at_utc|datetime_utc|utc", "source|string|source_id", "classification|string|classification", "proxy_label|string|label|nullable"),
        ("observation_date", "series_id", "instrument_id", "source", "available_at_utc"),
        ("observation_date", "series_id", "instrument_id", "source", "available_at_utc"), "daily by year", "retain with input manifests",
        ("strategy.spread", "strategy.signal_generation", "backtesting.engine"),
        ("one_market_identity", "available_not_before_observation", "classification_lineage"),
    ),
    "paper_quotes": _schema(
        "paper_quotes", "data/paper/agent_N/run_id/quotes.csv",
        ("timestamp_utc|datetime_utc|utc", "instrument_id|string|instrument_id", "bid_price|decimal|price_points", "ask_price|decimal|price_points", "bid_size|decimal|contracts", "ask_size|decimal|contracts"),
        ("timestamp_utc", "instrument_id"), ("timestamp_utc", "instrument_id"), "event append within one paper run", "immutable after run close",
        ("agents.shared", "strategy.costs", "strategy.risk_signals"), ("positive_quote_fields", "bid_not_above_ask"),
    ),
    "paper_decisions": _schema(
        "paper_decisions", "data/paper/agent_N/run_id/decisions.csv",
        ("decision_id|string|decision_id", "timestamp_utc|datetime_utc|utc", "agent_id|string|agent_id", "strategy_version|string|version", "config_hash|string|sha256", "maturity|string|maturity", "prior_state|integer|state", "new_state|integer|state", "direction|integer|direction", "reason_code|string|reason_code", "signal_value|decimal|declared_by_signal_unit|nullable", "signal_unit|string|unit|nullable"),
        ("decision_id",), ("timestamp_utc", "decision_id"), "event append within one run", "immutable run artifact",
        ("agents.shared", "strategy.risk_signals"), ("state_direction_domain", "signal_pair"),
    ),
    "paper_orders": _schema(
        "paper_orders", "data/paper/agent_N/run_id/orders.csv",
        ("order_ref|string|order_ref", "decision_id|string|decision_id", "created_at_utc|datetime_utc|utc", "instrument_id|string|instrument_id", "side|string|side", "quantity|integer|contracts", "order_type|string|order_type", "time_in_force|string|time_in_force", "status|string|status", "ibkr_order_id|string|broker_order_id|nullable"),
        ("order_ref",), ("created_at_utc", "order_ref"), "event append/status reconciliation", "immutable after run close",
        ("agents.shared",), ("side_quantity_consistency",),
    ),
    "paper_fills": _schema(
        "paper_fills", "data/paper/agent_N/run_id/fills.csv",
        ("fill_id|string|fill_id", "order_ref|string|order_ref", "fill_time_utc|datetime_utc|utc", "instrument_id|string|instrument_id", "side|string|side", "quantity|integer|contracts", "fill_price|decimal|price_points", "commission_usd|decimal|usd"),
        ("fill_id",), ("fill_time_utc", "fill_id"), "event append within one run", "immutable after run close",
        ("agents.shared", "strategy.risk_signals"), ("side_quantity_consistency",),
    ),
    "paper_positions": _schema(
        "paper_positions", "data/paper/agent_N/run_id/positions.csv",
        ("timestamp_utc|datetime_utc|utc", "instrument_id|string|instrument_id", "quantity|integer|contracts", "average_cost|decimal|price_points", "market_price|decimal|price_points", "unrealized_pnl_usd|decimal|usd", "realized_pnl_usd|decimal|usd"),
        ("timestamp_utc", "instrument_id"), ("timestamp_utc", "instrument_id"), "snapshot append within one paper run", "immutable after run close",
        ("agents.shared", "strategy.risk_signals"),
    ),
    "backtest_daily": _schema(
        "backtest_daily", "data/results/backtests/run_id/daily.csv",
        ("observation_date|date|date", "gross_pnl_usd|decimal|usd", "transaction_cost_usd|decimal|usd", "financing_cost_usd|decimal|usd", "net_pnl_usd|decimal|usd", "equity_usd|decimal|usd", "drawdown_usd|decimal|usd", "drawdown_pct|decimal|percent", "gross_dv01_usd_per_bp|decimal|usd_per_bp", "net_dv01_usd_per_bp|decimal|usd_per_bp"),
        ("observation_date",), ("observation_date",), "daily within one backtest run", "immutable run artifact",
        ("backtesting.accounting", "backtesting.reports"),
    ),
    "backtest_decisions": _schema(
        "backtest_decisions", "data/results/backtests/run_id/decisions.csv",
        ("decision_id|string|decision_id", "timestamp_utc|datetime_utc|utc", "strategy_version|string|version", "config_hash|string|sha256", "maturity|string|maturity", "prior_state|integer|state", "new_state|integer|state", "direction|integer|direction", "reason_code|string|reason_code", "signal_value|decimal|declared_by_signal_unit|nullable", "signal_unit|string|unit|nullable"),
        ("decision_id",), ("timestamp_utc", "decision_id"), "event append within one backtest run", "immutable run artifact",
        ("backtesting.engine", "backtesting.reports"), ("state_direction_domain", "signal_pair"),
    ),
    "backtest_orders": _schema(
        "backtest_orders", "data/results/backtests/run_id/orders.csv",
        ("order_ref|string|order_ref", "decision_id|string|decision_id", "created_at_utc|datetime_utc|utc", "instrument_id|string|instrument_id", "side|string|side", "quantity|integer|contracts", "order_type|string|order_type", "time_in_force|string|time_in_force", "status|string|status"),
        ("order_ref",), ("created_at_utc", "order_ref"), "event append within one backtest run", "immutable run artifact",
        ("backtesting.engine", "backtesting.reports"), ("side_quantity_consistency",),
    ),
    "backtest_fills": _schema(
        "backtest_fills", "data/results/backtests/run_id/fills.csv",
        ("fill_id|string|fill_id", "order_ref|string|order_ref", "fill_time_utc|datetime_utc|utc", "instrument_id|string|instrument_id", "side|string|side", "quantity|integer|contracts", "fill_price|decimal|price_points", "commission_usd|decimal|usd"),
        ("fill_id",), ("fill_time_utc", "fill_id"), "event append within one backtest run", "immutable run artifact",
        ("backtesting.accounting", "backtesting.reports"), ("side_quantity_consistency",),
    ),
    "backtest_trades": _schema(
        "backtest_trades", "data/results/backtests/run_id/trades.csv",
        ("trade_id|string|trade_id", "decision_id|string|decision_id", "maturity|string|maturity", "direction|integer|direction", "opened_at_utc|datetime_utc|utc", "closed_at_utc|datetime_utc|utc|nullable", "gross_pnl_usd|decimal|usd", "cost_usd|decimal|usd", "net_pnl_usd|decimal|usd"),
        ("trade_id",), ("opened_at_utc", "trade_id"), "event append within one backtest run", "immutable run artifact",
        ("backtesting.accounting", "backtesting.reports"), ("trade_direction_domain",),
    ),
    "backtest_positions": _schema(
        "backtest_positions", "data/results/backtests/run_id/positions.csv",
        ("observation_date|date|date", "instrument_id|string|instrument_id", "quantity|integer|contracts", "market_price|decimal|price_points", "market_value_usd|decimal|usd", "unrealized_pnl_usd|decimal|usd", "realized_pnl_usd|decimal|usd"),
        ("observation_date", "instrument_id"), ("observation_date", "instrument_id"), "daily within one backtest run", "immutable run artifact",
        ("backtesting.accounting", "backtesting.reports"),
    ),
    "backtest_summary": _schema(
        "backtest_summary", "data/results/backtests/run_id/summary.csv",
        ("run_id|string|run_id", "strategy_version|string|version", "start_date|date|date", "end_date|date|date", "row_count|integer|rows", "trade_count|integer|trades", "net_pnl_usd|decimal|usd", "ending_equity_usd|decimal|usd", "max_drawdown_usd|decimal|usd", "max_drawdown_pct|decimal|percent"),
        ("run_id",), ("run_id",), "once per completed backtest run", "immutable run artifact",
        ("backtesting.reports",),
    ),
    "run_manifest": _schema(
        "run_manifest", "data/manifests/run_id.csv",
        ("run_id|string|run_id", "run_type|string|run_type", "agent_id|string|agent_id|nullable", "strategy_version|string|version", "config_hash|string|sha256", "code_commit|string|git_commit", "input_manifest_hash|string|sha256", "started_at_utc|datetime_utc|utc", "ended_at_utc|datetime_utc|utc|nullable", "row_count|integer|rows", "status|string|status"),
        ("run_id",), ("run_id",), "one row per run", "permanent audit record",
        ("backtesting.reports", "agents.shared", "data_pipeline.manifests"),
    ),
    "run_inputs": _schema(
        "run_inputs", "data/manifests/run_id_inputs.csv",
        ("run_id|string|run_id", "path|string|repo_relative_path", "sha256|string|sha256", "row_count|integer|rows", "start_time|string|date_or_utc", "end_time|string|date_or_utc", "schema_version|string|version"),
        ("run_id", "path"), ("run_id", "path"), "one row per run input", "permanent audit record",
        ("backtesting.reports", "agents.shared", "data_pipeline.manifests"),
    ),
}


MIGRATION_RULES = (
    MigrationRule("eris_vendor_cache", r"data/cache/eris_sofr_settlements_v3/[^/]+\.csv", "keep immutable source", FUTURES_SOURCE_PATH, "one output row per unique approved instrument/date key; source rows remain unchanged", "64 vendor columns to 4-5 consumed source columns", "use untouched cache file and its P20 SHA-256", f"Symbol matches {APPROVED_ERIS_SYMBOL_PATTERN}; filename matches {ERIS_SETTLEMENT_FILENAME_PATTERN}; EvaluationDate equals the parsed YYYYMMDD date; block the complete file on a duplicate (EvaluationDate,Symbol) key", "for every file, output keys equal exactly the unique allowed (EvaluationDate,Symbol) keys and output count equals that key-set size"),
    MigrationRule("cme_swap_master", r"data/cme_swap_data\.csv", "regenerate", f"{FUTURES_SOURCE_PATH} and data/canonical/reference/contract_risk_YYYY.csv", "2,948 source rows split by year without silent loss", "4 current columns mapped to settlement and risk contracts", "retain current file until staged hashes and spot checks pass"),
    MigrationRule("treasury_futures_master", r"data/treasury_futures_data\.csv", "regenerate", f"{FUTURES_SOURCE_PATH} and data/canonical/reference/contract_risk_YYYY.csv", "2,942 proxy rows retained with explicit proxy lineage", "4 current columns mapped to settlement and risk contracts", "retain current file until staged hashes and spot checks pass"),
    MigrationRule("swap_rates", r"data/swap_rates\.csv", "regenerate", "data/canonical/market/daily_market_YYYY.csv", "1,474 dates expand to long consumed price observations", "5 wide columns become long market rows; returns are recomputed features", "retain current file until staged row reconciliation passes"),
    MigrationRule("treasury_futures", r"data/treasury_futures\.csv", "regenerate", "data/canonical/market/daily_market_YYYY.csv", "1,471 dates expand to long proxy price observations", "5 wide columns become long market rows; returns are recomputed features", "retain current file until staged row reconciliation passes"),
    MigrationRule("treasury_rates", r"data/treasury_rates\.csv", "regenerate", f"{RATE_SOURCE_PATH} and data/canonical/market/daily_market_YYYY.csv", "2,143 dates expand to one long row per present consumed series", "16 wide columns become 5-column source and 10-column canonical rows", "retain current file until staged row and value spot checks pass"),
    MigrationRule("raw_wide", r"data/raw_price_data\.csv", "supersede after validation", "data/canonical/market/daily_market_YYYY.csv", "2,154 dates reconstructed by keyed long observations", "24 copied columns replaced by narrow long rows", "restore or continue using untouched current file"),
    MigrationRule("signal_wide", r"data/signal_data\.csv", "supersede after validation", "data/results/backtests/run_id/decisions.csv", "1,471 historical proxy dates remain reproducible as legacy evidence", "40 copied/feature columns replaced by market inputs plus decision rows", "restore or continue using untouched current file"),
    MigrationRule("risk_wide", r"data/risk_data\.csv", "supersede after validation", "data/results/backtests/run_id/positions.csv and decisions.csv", "1,471 historical proxy dates remain reproducible as legacy evidence", "72 copied/risk columns replaced by narrow decision and position rows", "restore or continue using untouched current file"),
    MigrationRule("legacy_backtests", r"data/swap_arb_backtest_.+\.csv", "archive labelled legacy", "data/results/legacy_proxy/<original_filename>", "preserve each original row count: 2,148; 1,471; 756; or 753", "retain original 85/99-column shape under explicit legacy-proxy label", "move archived file back by exact original name; P20 hash proves identity"),
    MigrationRule("r2_inventory", r"r2_objects\.csv", "keep immutable source", "r2_objects.csv (in place; excluded from canonical manifests)", "preserve all 2,117 metadata rows", "retain 9 inventory columns; never treat as market input", "use untouched top-level manifest and its P20 SHA-256"),
)


def _parse_value(column: ColumnContract, value: str, row_number: int) -> object:
    if value == "":
        if column.nullable:
            return None
        raise SchemaValidationError(f"row {row_number}: {column.name} is required")
    try:
        if column.scalar_type == "string":
            return value
        if column.scalar_type == "integer":
            if re.fullmatch(r"[+-]?\d+", value) is None:
                raise ValueError
            return int(value)
        if column.scalar_type == "decimal":
            parsed = Decimal(value)
            if not parsed.is_finite():
                raise ValueError
            return parsed
        if column.scalar_type == "date":
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
                raise ValueError
            return date.fromisoformat(value)
        if column.scalar_type == "datetime_utc":
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value) is None:
                raise ValueError
            parsed = datetime.fromisoformat(value[:-1] + "+00:00")
            if parsed.utcoffset().total_seconds() != 0:
                raise ValueError
            return parsed
    except (InvalidOperation, TypeError, ValueError) as error:
        raise SchemaValidationError(f"row {row_number}: invalid {column.name}") from error
    raise SchemaValidationError(f"row {row_number}: invalid scalar type {column.scalar_type}")


def _validate_row_rules(contract: CsvContract, raw: dict[str, str], parsed: dict[str, object], row_number: int) -> None:
    rules = set(contract.validation_rules)
    if "one_market_identity" in rules and ((raw["series_id"] == "") == (raw["instrument_id"] == "")):
        raise SchemaValidationError(f"row {row_number}: exactly one market identity is required")
    if "available_not_before_observation" in rules and parsed["available_at_utc"] < parsed["source_observation_time_utc"]:
        raise SchemaValidationError(f"row {row_number}: available_at_utc precedes source observation")
    if "classification_lineage" in rules:
        classification, proxy = raw["classification"], raw["proxy_label"]
        if classification not in {"exact", "proxy", "assumed", "unavailable"}:
            raise SchemaValidationError(f"row {row_number}: invalid classification")
        if (classification == "proxy") != bool(proxy):
            raise SchemaValidationError(f"row {row_number}: proxy label must match classification")
    if "positive_quote_fields" in rules:
        for name in ("bid_price", "ask_price", "bid_size", "ask_size"):
            if parsed[name] <= 0:
                raise SchemaValidationError(f"row {row_number}: {name} must be positive")
    if "bid_not_above_ask" in rules and parsed["bid_price"] > parsed["ask_price"]:
        raise SchemaValidationError(f"row {row_number}: bid must not exceed ask")
    if "side_quantity_consistency" in rules:
        side, quantity = raw["side"], parsed["quantity"]
        if (side == "BUY" and quantity <= 0) or (side == "SELL" and quantity >= 0) or side not in {"BUY", "SELL"}:
            raise SchemaValidationError(f"row {row_number}: side and signed quantity disagree")
    if "rate_sensitivity_sign_domain" in rules and parsed["rate_sensitivity_sign"] not in {-1, 1}:
        raise SchemaValidationError(f"row {row_number}: rate_sensitivity_sign must be -1 or +1")
    if "positive_dv01_if_present" in rules and parsed["dv01_usd_per_bp"] is not None and parsed["dv01_usd_per_bp"] <= 0:
        raise SchemaValidationError(f"row {row_number}: dv01_usd_per_bp must be positive")
    if "state_direction_domain" in rules:
        if any(parsed[name] not in {-1, 0, 1} for name in ("prior_state", "new_state", "direction")):
            raise SchemaValidationError(f"row {row_number}: invalid state or direction")
    if "signal_pair" in rules and (raw["signal_value"] == "") != (raw["signal_unit"] == ""):
        raise SchemaValidationError(f"row {row_number}: signal_value and signal_unit must both be present or absent")
    if "trade_direction_domain" in rules and parsed["direction"] not in {-1, 1}:
        raise SchemaValidationError(f"row {row_number}: trade direction must be -1 or +1")


def _validate_csv_reader(contract: CsvContract, reader: csv.DictReader) -> int:
    expected = [column.name for column in contract.columns]
    if reader.fieldnames != expected:
        raise SchemaValidationError(f"header must equal {expected}")
    columns = {column.name: column for column in contract.columns}
    seen: set[tuple[str, ...]] = set()
    previous_order: tuple[object, ...] | None = None
    count = 0
    for row_number, raw in enumerate(reader, start=2):
        count += 1
        if None in raw or any(raw[name] is None for name in expected):
            raise SchemaValidationError(f"row {row_number}: row width differs from header")
        parsed = {name: _parse_value(columns[name], raw[name], row_number) for name in expected}
        key = tuple(raw[name] for name in contract.unique_key)
        if key in seen:
            raise SchemaValidationError(f"row {row_number}: duplicate key {key}")
        seen.add(key)
        order = tuple(parsed[name] if parsed[name] is not None else "" for name in contract.ordering)
        if previous_order is not None and order < previous_order:
            raise SchemaValidationError(f"row {row_number}: ordering violation")
        previous_order = order
        _validate_row_rules(contract, raw, parsed, row_number)
    return count


def validate_csv_bytes(contract: CsvContract, data: bytes) -> int:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise SchemaValidationError("CSV must be UTF-8") from error
    return _validate_csv_reader(contract, csv.DictReader(io.StringIO(text, newline="")))


def validate_csv(contract: CsvContract, path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return _validate_csv_reader(contract, csv.DictReader(handle))


def migration_rule_for(path: str) -> MigrationRule:
    matches = [rule for rule in MIGRATION_RULES if rule.matches(path)]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one migration rule for {path}; found {len(matches)}")
    return matches[0]
