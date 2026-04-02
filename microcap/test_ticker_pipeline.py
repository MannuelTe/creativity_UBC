import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

THIS_DIR = Path(__file__).parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from market_data import fetch_market_data_for_record
from ticker_utils import TickerManager, infer_ticker_record


class TickerPipelineTests(unittest.TestCase):
    def _build_manager(self, config_payload, extra_files):
        temp_dir = Path(tempfile.mkdtemp())
        config_path = temp_dir / "ticker_config.json"
        config_path.write_text(json.dumps(config_payload, indent=2))
        for filename, payload in extra_files.items():
            (temp_dir / filename).write_text(json.dumps(payload, indent=2))
        return TickerManager(str(config_path))

    def test_legacy_loader_accepts_string_arrays(self):
        manager = self._build_manager(
            config_payload={
                "exchanges": {
                    "TSX": {
                        "file": "tickers_tsx.json",
                        "name": "TSX",
                        "api_source": "yahoo_finance",
                        "enabled": True,
                    }
                },
                "validation": {},
                "settings": {},
            },
            extra_files={
                "tickers_tsx.json": {
                    "exchange": "TSX",
                    "suffix": ".TO",
                    "api_source": "yahoo_finance",
                    "tickers": ["SU", "CNQ"],
                }
            },
        )

        records = manager.get_exchange_records("TSX")
        self.assertEqual([record.display_symbol for record in records], ["SU.TO", "CNQ.TO"])
        self.assertEqual(records[0].query_symbols["alpha_vantage"], "SU")

    def test_object_schema_loader_deduplicates_by_canonical_symbol(self):
        manager = self._build_manager(
            config_payload={
                "exchanges": {
                    "US": {
                        "file": "tickers_us.json",
                        "name": "US",
                        "api_source": "yahoo_finance",
                        "enabled": True,
                    }
                },
                "validation": {},
                "settings": {},
            },
            extra_files={
                "tickers_us.json": {
                    "exchange": "US",
                    "api_source": "yahoo_finance",
                    "tickers": [
                        {
                            "canonical_symbol": "AAPL",
                            "base_symbol": "AAPL",
                            "display_symbol": "AAPL",
                            "exchange": "US",
                            "preferred_api": "yahoo_finance",
                            "fallback_apis": ["alpha_vantage"],
                            "query_symbols": {"yahoo": "AAPL", "alpha_vantage": "AAPL"},
                            "status": "active",
                            "aliases": [],
                        },
                        {
                            "canonical_symbol": "AAPL",
                            "base_symbol": "AAPL",
                            "display_symbol": "AAPL",
                            "exchange": "US",
                            "preferred_api": "yahoo_finance",
                            "fallback_apis": ["alpha_vantage"],
                            "query_symbols": {"yahoo": "AAPL", "alpha_vantage": "AAPL"},
                            "status": "active",
                            "aliases": [],
                        },
                    ],
                }
            },
        )

        records = manager.get_exchange_records("US")
        self.assertEqual(len(records), 1)
        self.assertTrue(any("Duplicate canonical symbol AAPL" in issue for issue in manager.get_config_issues()))

    def test_rank_records_prefers_small_cap_then_freshness(self):
        manager = self._build_manager(
            config_payload={"exchanges": {}, "validation": {}, "settings": {"max_market_cap_threshold_m": 1000}},
            extra_files={},
        )
        stale = infer_ticker_record("AAA")
        fresh = infer_ticker_record("BBB")
        ranked = manager.rank_records(
            [stale, fresh],
            validation_state={
                "AAA": {
                    "canonical_symbol": "AAA",
                    "small_cap_classification": "above_max_cap",
                    "quality_score": 90,
                    "last_success_at": "2026-04-01T00:00:00",
                },
                "BBB": {
                    "canonical_symbol": "BBB",
                    "small_cap_classification": "small_cap",
                    "quality_score": 40,
                    "last_success_at": "2026-04-02T00:00:00",
                },
            },
            max_market_cap_m=100,
        )
        self.assertEqual([record.display_symbol for record in ranked], ["BBB", "AAA"])

    def test_custom_inference_normalizes_common_symbol_forms(self):
        us = infer_ticker_record("aapl")
        tsx = infer_ticker_record("SU.TO")
        tsxv = infer_ticker_record("amk.v")
        self.assertEqual((us.exchange, us.display_symbol), ("US", "AAPL"))
        self.assertEqual((tsx.exchange, tsx.query_symbols["alpha_vantage"]), ("TSX", "SU"))
        self.assertEqual((tsxv.exchange, tsxv.preferred_api), ("TSXV", "alpha_vantage"))

    def test_provider_routing_uses_preferred_api_and_declared_fallback(self):
        settings = {
            "alpha_vantage_rate_limit_per_minute": 5,
            "provider_cache_ttl_seconds": 0,
            "request_retry_count": 1,
            "request_timeout_seconds": 1,
            "retry_backoff_seconds": 0,
            "yahoo_request_pause_seconds": 0,
        }
        tsx_record = infer_ticker_record("SU.TO", exchange="TSX")
        tsxv_record = infer_ticker_record("AMK.V", exchange="TSXV")

        with patch("market_data.fetch_yahoo_data") as yahoo_mock, patch("market_data.fetch_alpha_vantage_data") as alpha_mock:
            yahoo_mock.return_value = {"ok": True, "data": {"provider_used": "yahoo_finance"}}
            alpha_mock.return_value = {"ok": True, "data": {"provider_used": "alpha_vantage"}}

            fetch_market_data_for_record(tsx_record, settings, search_dir=THIS_DIR)
            yahoo_mock.assert_called_once()
            alpha_mock.assert_not_called()

        with patch("market_data.fetch_yahoo_data") as yahoo_mock, patch("market_data.fetch_alpha_vantage_data") as alpha_mock:
            alpha_mock.return_value = {"ok": True, "data": {"provider_used": "alpha_vantage"}}
            yahoo_mock.return_value = {"ok": True, "data": {"provider_used": "yahoo_finance"}}

            fetch_market_data_for_record(tsxv_record, settings, search_dir=THIS_DIR)
            alpha_mock.assert_called_once()
            yahoo_mock.assert_not_called()

        no_fallback_record = replace(infer_ticker_record("XYZ"), fallback_apis=())
        with patch("market_data.fetch_yahoo_data") as yahoo_mock, patch("market_data.fetch_alpha_vantage_data") as alpha_mock:
            yahoo_mock.return_value = {
                "ok": False,
                "failure": {"provider": "yahoo_finance", "provider_label": "Yahoo Finance", "reason": "not_found", "message": "missing"},
            }

            result = fetch_market_data_for_record(no_fallback_record, settings, search_dir=THIS_DIR)
            self.assertFalse(result["ok"])
            yahoo_mock.assert_called_once()
            alpha_mock.assert_not_called()

        fallback_record = infer_ticker_record("AMK.V", exchange="TSXV")
        with patch("market_data.fetch_yahoo_data") as yahoo_mock, patch("market_data.fetch_alpha_vantage_data") as alpha_mock:
            alpha_mock.return_value = {
                "ok": False,
                "failure": {"provider": "alpha_vantage", "provider_label": "Alpha Vantage", "reason": "not_found", "message": "missing"},
            }
            yahoo_mock.return_value = {"ok": True, "data": {"provider_used": "yahoo_finance"}}

            result = fetch_market_data_for_record(fallback_record, settings, search_dir=THIS_DIR)
            self.assertTrue(result["ok"])
            alpha_mock.assert_called_once()
            yahoo_mock.assert_called_once()

    def test_validation_snapshot_supports_legacy_list_and_new_mapping(self):
        manager = self._build_manager(
            config_payload={
                "exchanges": {
                    "US": {
                        "file": "tickers_us.json",
                        "name": "US",
                        "api_source": "yahoo_finance",
                        "enabled": True,
                    }
                },
                "validation": {
                    "valid_tickers_file": "tickers_validated.json",
                    "small_caps_file": "tickers_small_caps.json",
                },
                "settings": {"min_market_cap_threshold_m": 1, "max_market_cap_threshold_m": 1000},
            },
            extra_files={
                "tickers_us.json": {
                    "exchange": "US",
                    "api_source": "yahoo_finance",
                    "tickers": ["AAPL"],
                },
                "tickers_small_caps.json": {
                    "metadata": {},
                    "tickers": [
                        {
                            "ticker": "AAPL",
                            "market_cap": 100_000_000,
                            "validation_score": 80,
                            "validated_at": "2026-04-02T00:00:00",
                        }
                    ],
                },
                "tickers_validated.json": {
                    "metadata": {},
                    "tickers": {
                        "AAPL": {
                            "canonical_symbol": "AAPL",
                            "market_cap": 100_000_000,
                            "quality_score": 80,
                            "last_success_at": "2026-04-02T00:00:00",
                        }
                    },
                },
            },
        )

        small_caps = manager.get_validation_snapshot(use_small_caps_only=True)
        validated = manager.get_validation_snapshot(use_small_caps_only=False)
        self.assertIn("AAPL", small_caps)
        self.assertIn("AAPL", validated)


if __name__ == "__main__":
    unittest.main()
