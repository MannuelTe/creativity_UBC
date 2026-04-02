#!/usr/bin/env python3
"""
Ticker validation and snapshot generation for the microcap screener.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from market_data import (
    fetch_market_data_for_record,
    format_failure_reason,
    load_provider_settings,
)
from ticker_utils import TickerManager, TickerRecord


def _configure_logging(log_path: Path) -> logging.Logger:
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger


class TickerValidator:
    """Validates curated tickers and generates rich validation snapshots."""

    def __init__(self, config_file: str = "ticker_config.json"):
        self.manager = TickerManager(config_file=config_file)
        self.microcap_dir = self.manager.microcap_dir
        self.config_file = self.manager.config_file
        self.config = self.manager.config
        self.provider_settings = load_provider_settings(self.config)
        self.logger = _configure_logging(self.microcap_dir / "ticker_validation.log")

    def _classify_market_cap(self, market_cap: float) -> str:
        market_cap_m = market_cap / 1_000_000
        min_cap = self.config["settings"]["min_market_cap_threshold_m"]
        max_cap = self.config["settings"]["max_market_cap_threshold_m"]
        if market_cap_m < min_cap:
            return "below_min_cap"
        if market_cap_m > max_cap:
            return "above_max_cap"
        return "small_cap"

    def _build_snapshot_entry(self, record: TickerRecord, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "canonical_symbol": record.canonical_symbol,
            "ticker": record.canonical_symbol,
            "display_symbol": record.display_symbol,
            "base_symbol": record.base_symbol,
            "exchange": record.exchange,
            "company_name": payload.get("company_name") or record.company_name or payload.get("name"),
            "provider_used": payload.get("provider_used"),
            "market_cap": payload.get("market_cap", 0),
            "price": payload.get("current_price", 0),
            "sector": payload.get("sector", "N/A"),
            "industry": payload.get("industry", "N/A"),
            "revenue": payload.get("revenue", 0),
            "pe_ratio": payload.get("pe_ratio"),
            "quality_score": payload.get("quality_score", 0),
            "small_cap_classification": self._classify_market_cap(payload.get("market_cap", 0)),
            "preferred_api": record.preferred_api,
            "fallback_apis": list(record.fallback_apis),
            "query_symbols": dict(record.query_symbols),
            "last_success_at": payload.get("last_fetched_at") or datetime.utcnow().isoformat(),
            "last_failure_at": None,
            "failure_reason": None,
            "status": record.status,
            "aliases": list(record.aliases),
        }

    def _build_failure_entry(self, record: TickerRecord, failure: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "canonical_symbol": record.canonical_symbol,
            "ticker": record.canonical_symbol,
            "display_symbol": record.display_symbol,
            "exchange": record.exchange,
            "provider_used": failure.get("provider"),
            "query_symbol": failure.get("query_symbol"),
            "reason": failure.get("reason"),
            "message": failure.get("message"),
            "last_failure_at": failure.get("timestamp") or datetime.utcnow().isoformat(),
        }

    def validate_exchange(self, exchange: str) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
        self.logger.info("Validating exchange %s", exchange)
        records = self.manager.get_exchange_records(exchange)
        if not records:
            self.logger.info("No active records found for exchange %s", exchange)
            return {}, {}

        valid_entries: Dict[str, Dict[str, Any]] = {}
        failed_entries: Dict[str, Dict[str, Any]] = {}

        for index, record in enumerate(records, start=1):
            self.logger.info(
                "Validating %s (%s/%s) using %s",
                record.display_symbol,
                index,
                len(records),
                record.preferred_api,
            )
            result = fetch_market_data_for_record(
                record=record,
                settings=self.provider_settings,
                search_dir=self.microcap_dir,
            )

            if result.get("ok"):
                snapshot_entry = self._build_snapshot_entry(record, result["data"])
                valid_entries[record.canonical_symbol] = snapshot_entry
                self.logger.info(
                    "Validated %s via %s ($%s market cap)",
                    record.display_symbol,
                    snapshot_entry["provider_used"],
                    f"{snapshot_entry['market_cap']:,.0f}",
                )
                continue

            failure = self._build_failure_entry(record, result["failure"])
            failed_entries[record.canonical_symbol] = failure
            self.logger.warning(
                "Failed %s: %s",
                record.display_symbol,
                format_failure_reason(result["failure"], result.get("failures")),
            )

        self.logger.info(
            "Exchange %s validation complete: %s valid, %s failed",
            exchange,
            len(valid_entries),
            len(failed_entries),
        )
        return valid_entries, failed_entries

    def validate_all_exchanges(self) -> Dict[str, Any]:
        start_time = datetime.utcnow()
        all_validated: Dict[str, Dict[str, Any]] = {}
        all_failed: Dict[str, Dict[str, Dict[str, Any]]] = {}

        for exchange in self.config["exchanges"]:
            valid_entries, failed_entries = self.validate_exchange(exchange)
            all_validated.update(valid_entries)
            all_failed[exchange] = failed_entries

        small_caps = {
            symbol: entry
            for symbol, entry in all_validated.items()
            if entry.get("small_cap_classification") == "small_cap"
        }
        excluded = {
            symbol: entry
            for symbol, entry in all_validated.items()
            if entry.get("small_cap_classification") != "small_cap"
        }

        duration_seconds = (datetime.utcnow() - start_time).total_seconds()
        results = {
            "validation_summary": {
                "schema_version": "2.0",
                "total_validated": len(all_validated),
                "small_caps_found": len(small_caps),
                "excluded_not_small_cap": len(excluded),
                "total_failed": sum(len(entries) for entries in all_failed.values()),
                "validation_date": start_time.isoformat(),
                "duration_seconds": duration_seconds,
                "config_issues": self.manager.get_config_issues(),
            },
            "small_cap_tickers": small_caps,
            "all_validated_tickers": all_validated,
            "failed_tickers": all_failed,
            "excluded_tickers": excluded,
        }

        self.logger.info(
            "Validation complete in %.1f seconds: %s small caps, %s total valid",
            duration_seconds,
            len(small_caps),
            len(all_validated),
        )
        return results

    def save_results(self, results: Dict[str, Any]) -> Dict[str, str]:
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        small_caps_file = self.microcap_dir / f"tickers_small_caps_{timestamp}.json"
        all_validated_file = self.microcap_dir / f"tickers_validated_{timestamp}.json"
        report_file = self.microcap_dir / f"validation_report_{timestamp}.json"

        with open(small_caps_file, "w") as file_obj:
            json.dump(
                {
                    "metadata": results["validation_summary"],
                    "tickers": results["small_cap_tickers"],
                },
                file_obj,
                indent=2,
            )

        with open(all_validated_file, "w") as file_obj:
            json.dump(
                {
                    "metadata": results["validation_summary"],
                    "tickers": results["all_validated_tickers"],
                },
                file_obj,
                indent=2,
            )

        with open(report_file, "w") as file_obj:
            json.dump(results, file_obj, indent=2)

        self.config["validation"]["last_run"] = results["validation_summary"]["validation_date"]
        self.config["validation"]["small_caps_file"] = small_caps_file.name
        self.config["validation"]["valid_tickers_file"] = all_validated_file.name
        self.config["last_updated"] = datetime.utcnow().date().isoformat()

        with open(self.config_file, "w") as file_obj:
            json.dump(self.config, file_obj, indent=2)

        return {
            "small_caps_file": small_caps_file.name,
            "validated_file": all_validated_file.name,
            "report_file": report_file.name,
        }


def main() -> None:
    print("🔍 Ticker Validation System")
    print("=" * 50)

    validator = TickerValidator()

    try:
        results = validator.validate_all_exchanges()
        files = validator.save_results(results)

        print("\n✅ Validation Complete!")
        print("📊 Summary:")
        print(f"   - Small caps found: {results['validation_summary']['small_caps_found']}")
        print(f"   - Total validated: {results['validation_summary']['total_validated']}")
        print(f"   - Failed validation: {results['validation_summary']['total_failed']}")
        print(f"   - Duration: {results['validation_summary']['duration_seconds']:.1f} seconds")
        print("\n📁 Files created:")
        print(f"   - Small caps: {files['small_caps_file']}")
        print(f"   - All validated: {files['validated_file']}")
        print(f"   - Full report: {files['report_file']}")
    except KeyboardInterrupt:
        print("\n❌ Validation interrupted by user")
    except Exception as exc:  # noqa: BLE001
        validator.logger.error("Validation failed: %s", exc)
        print(f"\n❌ Validation failed: {exc}")


if __name__ == "__main__":
    main()
