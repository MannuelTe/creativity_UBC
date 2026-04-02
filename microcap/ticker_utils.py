"""
Ticker management utilities for the microcap screener.

This module normalizes curated ticker JSON files into rich records that can be
used consistently by the app and the batch validator.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

DEFAULT_API_BY_EXCHANGE = {
    "US": "yahoo_finance",
    "TSX": "yahoo_finance",
    "TSXV": "alpha_vantage",
}

DEFAULT_FALLBACK_APIS = {
    "US": ("alpha_vantage",),
    "TSX": ("alpha_vantage",),
    "TSXV": ("yahoo_finance",),
}

DEFAULT_SUFFIX_BY_EXCHANGE = {
    "US": "",
    "TSX": ".TO",
    "TSXV": ".V",
}

KNOWN_SUFFIXES = {
    ".TO": "TSX",
    ".V": "TSXV",
}

ACTIVE_STATUSES = {"active"}


@dataclass(frozen=True)
class TickerRecord:
    canonical_symbol: str
    base_symbol: str
    display_symbol: str
    exchange: str
    preferred_api: str
    fallback_apis: Tuple[str, ...]
    query_symbols: Dict[str, str]
    status: str
    aliases: Tuple[str, ...] = ()
    company_name: str = ""
    priority: int = 0
    tags: Tuple[str, ...] = ()
    notes: str = ""
    manual_exclude_reason: str = ""
    source_file: str = ""
    source_index: int = 0

    @property
    def is_active(self) -> bool:
        return self.status.lower() in ACTIVE_STATUSES and not self.manual_exclude_reason


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def _infer_exchange_from_symbol(symbol: str) -> str:
    normalized = _normalize_symbol(symbol)
    for suffix, exchange in KNOWN_SUFFIXES.items():
        if normalized.endswith(suffix):
            return exchange
    return "US"


def _strip_suffix(symbol: str) -> str:
    normalized = _normalize_symbol(symbol)
    for suffix in KNOWN_SUFFIXES:
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)]
    return normalized


def _normalize_aliases(symbols: Iterable[str], canonical_symbol: str, base_symbol: str) -> Tuple[str, ...]:
    aliases: List[str] = []
    for symbol in symbols:
        normalized = _normalize_symbol(symbol)
        if normalized and normalized not in aliases and normalized != canonical_symbol:
            aliases.append(normalized)
    if base_symbol != canonical_symbol and base_symbol not in aliases:
        aliases.append(base_symbol)
    return tuple(aliases)


def infer_ticker_record(
    symbol: str,
    exchange: Optional[str] = None,
    preferred_api: Optional[str] = None,
    fallback_apis: Optional[Sequence[str]] = None,
    source_file: str = "custom_input",
    source_index: int = 0,
) -> TickerRecord:
    normalized = _normalize_symbol(symbol)
    inferred_exchange = exchange or _infer_exchange_from_symbol(normalized)
    suffix = DEFAULT_SUFFIX_BY_EXCHANGE.get(inferred_exchange, "")
    if suffix and not normalized.endswith(suffix):
        canonical_symbol = f"{normalized}{suffix}"
    else:
        canonical_symbol = normalized

    base_symbol = _strip_suffix(canonical_symbol)
    selected_preferred_api = preferred_api or DEFAULT_API_BY_EXCHANGE.get(inferred_exchange, "yahoo_finance")
    selected_fallbacks = list(
        fallback_apis if fallback_apis is not None else DEFAULT_FALLBACK_APIS.get(inferred_exchange, ())
    )
    selected_fallbacks = [api for api in selected_fallbacks if api and api != selected_preferred_api]

    return TickerRecord(
        canonical_symbol=canonical_symbol,
        base_symbol=base_symbol,
        display_symbol=canonical_symbol,
        exchange=inferred_exchange,
        preferred_api=selected_preferred_api,
        fallback_apis=tuple(dict.fromkeys(selected_fallbacks)),
        query_symbols={
            "yahoo": canonical_symbol,
            "alpha_vantage": base_symbol,
        },
        status="active",
        aliases=_normalize_aliases((base_symbol,), canonical_symbol, base_symbol),
        source_file=source_file,
        source_index=source_index,
    )


class TickerManager:
    """Manages curated ticker universes and validation snapshots."""

    def __init__(self, config_file: str = "ticker_config.json"):
        self.microcap_dir = Path(__file__).parent
        raw_config_path = Path(config_file)
        self.config_file = raw_config_path if raw_config_path.is_absolute() else self.microcap_dir / raw_config_path
        self.config_dir = self.config_file.parent
        self.config = self._load_config()
        self._cached_records: Dict[str, List[TickerRecord]] = {}
        self._validation_cache: Dict[bool, Dict[str, Dict[str, Any]]] = {}
        self._config_issues: List[str] = []

    def _resolve_file_path(self, filename: str) -> Path:
        path = Path(filename)
        if path.is_absolute():
            return path
        return self.config_dir / path

    def _load_json_file(self, path: Path, empty_fallback: Any) -> Any:
        try:
            with open(path, "r") as file_obj:
                return json.load(file_obj)
        except FileNotFoundError:
            logger.error("JSON file not found: %s", path)
            return empty_fallback
        except json.JSONDecodeError as exc:
            logger.error("Invalid JSON in %s: %s", path, exc)
            return empty_fallback

    def _load_config(self) -> Dict[str, Any]:
        config = self._load_json_file(self.config_file, {"exchanges": {}, "validation": {}, "settings": {}})
        config.setdefault("exchanges", {})
        config.setdefault("validation", {})
        config.setdefault("settings", {})
        return config

    def _record_from_legacy_symbol(
        self,
        raw_symbol: str,
        exchange: str,
        exchange_config: Dict[str, Any],
        ticker_payload: Dict[str, Any],
        source_file: str,
        source_index: int,
    ) -> TickerRecord:
        suffix = ticker_payload.get("suffix") or DEFAULT_SUFFIX_BY_EXCHANGE.get(exchange, "")
        symbol = _normalize_symbol(raw_symbol)
        if suffix and not symbol.endswith(suffix):
            symbol = f"{symbol}{suffix}"
        return infer_ticker_record(
            symbol=symbol,
            exchange=exchange,
            preferred_api=exchange_config.get("api_source") or ticker_payload.get("api_source"),
            source_file=source_file,
            source_index=source_index,
        )

    def _record_from_object(
        self,
        record_data: Dict[str, Any],
        exchange: str,
        exchange_config: Dict[str, Any],
        ticker_payload: Dict[str, Any],
        source_file: str,
        source_index: int,
    ) -> TickerRecord:
        fallback_exchange = record_data.get("exchange") or exchange
        suffix = ticker_payload.get("suffix") or DEFAULT_SUFFIX_BY_EXCHANGE.get(fallback_exchange, "")
        raw_canonical = record_data.get("canonical_symbol") or record_data.get("display_symbol") or record_data.get("base_symbol")
        if not raw_canonical:
            raise ValueError(f"Ticker record missing symbol fields in {source_file}:{source_index}")

        canonical_symbol = _normalize_symbol(raw_canonical)
        if suffix and fallback_exchange in {"TSX", "TSXV"} and not canonical_symbol.endswith(suffix):
            canonical_symbol = f"{canonical_symbol}{suffix}"

        base_symbol = _normalize_symbol(record_data.get("base_symbol") or _strip_suffix(canonical_symbol))
        display_symbol = _normalize_symbol(record_data.get("display_symbol") or canonical_symbol)
        preferred_api = record_data.get("preferred_api") or exchange_config.get("api_source") or ticker_payload.get("api_source")
        preferred_api = preferred_api or DEFAULT_API_BY_EXCHANGE.get(fallback_exchange, "yahoo_finance")
        fallback_apis = tuple(
            dict.fromkeys(
                api
                for api in record_data.get("fallback_apis", DEFAULT_FALLBACK_APIS.get(fallback_exchange, ()))
                if api and api != preferred_api
            )
        )

        query_symbols = dict(record_data.get("query_symbols", {}))
        query_symbols.setdefault("yahoo", display_symbol)
        query_symbols.setdefault("alpha_vantage", base_symbol)

        aliases = _normalize_aliases(
            record_data.get("aliases", ()),
            canonical_symbol=canonical_symbol,
            base_symbol=base_symbol,
        )

        return TickerRecord(
            canonical_symbol=canonical_symbol,
            base_symbol=base_symbol,
            display_symbol=display_symbol,
            exchange=fallback_exchange,
            preferred_api=preferred_api,
            fallback_apis=fallback_apis,
            query_symbols=query_symbols,
            status=(record_data.get("status") or "active").lower(),
            aliases=aliases,
            company_name=record_data.get("company_name", "") or "",
            priority=int(record_data.get("priority", 0) or 0),
            tags=tuple(record_data.get("tags", ()) or ()),
            notes=record_data.get("notes", "") or "",
            manual_exclude_reason=record_data.get("manual_exclude_reason", "") or "",
            source_file=source_file,
            source_index=source_index,
        )

    def _normalize_record(
        self,
        raw_record: Any,
        exchange: str,
        exchange_config: Dict[str, Any],
        ticker_payload: Dict[str, Any],
        source_file: str,
        source_index: int,
    ) -> TickerRecord:
        if isinstance(raw_record, str):
            return self._record_from_legacy_symbol(
                raw_symbol=raw_record,
                exchange=exchange,
                exchange_config=exchange_config,
                ticker_payload=ticker_payload,
                source_file=source_file,
                source_index=source_index,
            )
        if isinstance(raw_record, dict):
            return self._record_from_object(
                record_data=raw_record,
                exchange=exchange,
                exchange_config=exchange_config,
                ticker_payload=ticker_payload,
                source_file=source_file,
                source_index=source_index,
            )
        raise ValueError(f"Unsupported ticker record type in {source_file}:{source_index}")

    def get_exchange_records(self, exchange: str, include_inactive: bool = False) -> List[TickerRecord]:
        if exchange in self._cached_records:
            records = self._cached_records[exchange]
            return list(records if include_inactive else [record for record in records if record.is_active])

        exchange_config = self.config["exchanges"].get(exchange)
        if not exchange_config or not exchange_config.get("enabled", True):
            return []

        ticker_file = exchange_config.get("file")
        if not ticker_file:
            issue = f"Exchange {exchange} is missing a ticker file"
            self._config_issues.append(issue)
            logger.warning(issue)
            return []

        file_path = self._resolve_file_path(ticker_file)
        ticker_payload = self._load_json_file(file_path, {})
        raw_records = ticker_payload.get("tickers", [])
        if not isinstance(raw_records, list):
            issue = f"Ticker file {file_path} has a non-list 'tickers' payload"
            self._config_issues.append(issue)
            logger.warning(issue)
            return []

        normalized_records: List[TickerRecord] = []
        seen_symbols: set[str] = set()
        for source_index, raw_record in enumerate(raw_records):
            try:
                record = self._normalize_record(
                    raw_record=raw_record,
                    exchange=exchange,
                    exchange_config=exchange_config,
                    ticker_payload=ticker_payload,
                    source_file=str(file_path.name),
                    source_index=source_index,
                )
            except ValueError as exc:
                issue = str(exc)
                self._config_issues.append(issue)
                logger.warning(issue)
                continue

            if record.canonical_symbol in seen_symbols:
                issue = (
                    f"Duplicate canonical symbol {record.canonical_symbol} in {file_path.name}; "
                    "keeping the first occurrence"
                )
                self._config_issues.append(issue)
                logger.warning(issue)
                continue

            seen_symbols.add(record.canonical_symbol)
            normalized_records.append(record)

        self._cached_records[exchange] = normalized_records
        return list(normalized_records if include_inactive else [record for record in normalized_records if record.is_active])

    def merge_records(self, records: Iterable[TickerRecord], prefer: str = "first") -> List[TickerRecord]:
        merged: Dict[str, TickerRecord] = {}
        order: List[str] = []

        for record in records:
            if record.canonical_symbol not in merged:
                order.append(record.canonical_symbol)
                merged[record.canonical_symbol] = record
                continue

            logger.warning("Duplicate canonical symbol %s encountered during merge", record.canonical_symbol)
            if prefer == "last":
                merged[record.canonical_symbol] = record

        return [merged[symbol] for symbol in order]

    def build_custom_records(self, symbols: Iterable[str]) -> List[TickerRecord]:
        custom_records = [
            infer_ticker_record(symbol, source_index=index)
            for index, symbol in enumerate(symbols)
            if _normalize_symbol(symbol)
        ]
        return self.merge_records(custom_records, prefer="last")

    def get_all_records(
        self,
        selected_exchanges: Optional[Sequence[str]] = None,
        include_inactive: bool = False,
    ) -> List[TickerRecord]:
        exchanges = selected_exchanges or list(self.config["exchanges"].keys())
        records: List[TickerRecord] = []
        for exchange in exchanges:
            records.extend(self.get_exchange_records(exchange, include_inactive=include_inactive))
        return self.merge_records(records)

    def get_exchange_tickers(self, exchange: str) -> List[str]:
        return [record.display_symbol for record in self.get_exchange_records(exchange)]

    def _snapshot_path(self, use_small_caps_only: bool = True) -> Optional[Path]:
        validation_config = self.config.get("validation", {})
        filename = validation_config.get("small_caps_file" if use_small_caps_only else "valid_tickers_file")
        if not filename:
            return None
        return self._resolve_file_path(filename)

    def _normalize_snapshot_entry(self, symbol: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        entry = dict(payload)
        canonical_symbol = _normalize_symbol(
            entry.get("canonical_symbol")
            or entry.get("ticker")
            or entry.get("symbol")
            or symbol
        )
        entry["canonical_symbol"] = canonical_symbol
        entry["ticker"] = canonical_symbol
        entry.setdefault("last_success_at", entry.get("validated_at"))
        entry.setdefault("provider_used", entry.get("api_source") or entry.get("data_source"))
        entry.setdefault("quality_score", entry.get("validation_score", 0))

        if "small_cap_classification" not in entry:
            market_cap = entry.get("market_cap")
            try:
                market_cap_m = float(market_cap) / 1_000_000
            except (TypeError, ValueError):
                market_cap_m = None

            min_cap = self.config.get("settings", {}).get("min_market_cap_threshold_m")
            max_cap = self.config.get("settings", {}).get("max_market_cap_threshold_m")
            if market_cap_m is None or min_cap is None or max_cap is None:
                entry["small_cap_classification"] = "unknown"
            elif min_cap <= market_cap_m <= max_cap:
                entry["small_cap_classification"] = "small_cap"
            elif market_cap_m < min_cap:
                entry["small_cap_classification"] = "below_min_cap"
            else:
                entry["small_cap_classification"] = "above_max_cap"

        return entry

    def get_validation_snapshot(self, use_small_caps_only: bool = True) -> Dict[str, Dict[str, Any]]:
        cache_key = bool(use_small_caps_only)
        if cache_key in self._validation_cache:
            return dict(self._validation_cache[cache_key])

        path = self._snapshot_path(use_small_caps_only=use_small_caps_only)
        if not path or not path.exists():
            self._validation_cache[cache_key] = {}
            return {}

        payload = self._load_json_file(path, {})
        tickers_payload = payload.get("tickers", {})
        snapshot: Dict[str, Dict[str, Any]] = {}

        if isinstance(tickers_payload, dict):
            for symbol, entry in tickers_payload.items():
                if isinstance(entry, dict):
                    normalized = self._normalize_snapshot_entry(symbol, entry)
                else:
                    normalized = self._normalize_snapshot_entry(symbol, {"canonical_symbol": symbol})
                snapshot[normalized["canonical_symbol"]] = normalized
        elif isinstance(tickers_payload, list):
            for index, entry in enumerate(tickers_payload):
                if isinstance(entry, dict):
                    symbol = entry.get("ticker") or entry.get("canonical_symbol") or entry.get("symbol")
                    if not symbol:
                        logger.warning("Skipping validation entry %s in %s without ticker field", index, path.name)
                        continue
                    normalized = self._normalize_snapshot_entry(symbol, entry)
                elif isinstance(entry, str):
                    normalized = self._normalize_snapshot_entry(entry, {"canonical_symbol": entry})
                else:
                    logger.warning("Skipping unsupported validation entry %s in %s", index, path.name)
                    continue
                snapshot[normalized["canonical_symbol"]] = normalized

        self._validation_cache[cache_key] = snapshot
        return dict(snapshot)

    def _record_sort_key(
        self,
        record: TickerRecord,
        validation_state: Dict[str, Dict[str, Any]],
        max_market_cap_m: Optional[float],
    ) -> Tuple[Any, ...]:
        snapshot = validation_state.get(record.canonical_symbol, {})
        priority = int(record.priority or 0)
        quality_score = float(snapshot.get("quality_score") or 0)
        last_success_at = snapshot.get("last_success_at") or ""
        small_cap_classification = snapshot.get("small_cap_classification", "unknown")
        market_cap = snapshot.get("market_cap")

        freshness_rank = 0.0
        if last_success_at:
            try:
                freshness_rank = datetime.fromisoformat(last_success_at.replace("Z", "+00:00")).timestamp()
            except ValueError:
                freshness_rank = 0.0

        small_cap_rank = 0
        if small_cap_classification == "small_cap":
            small_cap_rank = 2
        elif max_market_cap_m is not None and market_cap:
            try:
                if float(market_cap) / 1_000_000 <= max_market_cap_m:
                    small_cap_rank = 1
            except (TypeError, ValueError):
                small_cap_rank = 0

        validation_presence_rank = 1 if snapshot else 0

        return (
            -priority,
            -small_cap_rank,
            -validation_presence_rank,
            -freshness_rank,
            -(quality_score or 0),
            record.source_index,
            record.canonical_symbol,
        )

    def rank_records(
        self,
        records: Sequence[TickerRecord],
        validation_state: Optional[Dict[str, Dict[str, Any]]] = None,
        max_market_cap_m: Optional[float] = None,
        include_inactive: bool = False,
    ) -> List[TickerRecord]:
        validation_snapshot = validation_state or {}
        eligible_records = list(records if include_inactive else [record for record in records if record.is_active])
        return sorted(
            eligible_records,
            key=lambda record: self._record_sort_key(
                record=record,
                validation_state=validation_snapshot,
                max_market_cap_m=max_market_cap_m,
            ),
        )

    def get_validated_records(self, use_small_caps_only: bool = True) -> List[TickerRecord]:
        validation_snapshot = self.get_validation_snapshot(use_small_caps_only=use_small_caps_only)
        if not validation_snapshot:
            return []

        curated_records = {
            record.canonical_symbol: record
            for record in self.get_all_records(include_inactive=True)
        }

        resolved_records: List[TickerRecord] = []
        for symbol, snapshot in validation_snapshot.items():
            if use_small_caps_only and snapshot.get("small_cap_classification") not in {"small_cap", "unknown"}:
                continue
            resolved_records.append(
                curated_records.get(symbol)
                or infer_ticker_record(
                    symbol=symbol,
                    exchange=snapshot.get("exchange"),
                    source_file="validation_snapshot",
                )
            )

        return self.rank_records(resolved_records, validation_state=validation_snapshot, include_inactive=False)

    def get_validated_tickers(self, use_small_caps_only: bool = True) -> List[str]:
        return [record.display_symbol for record in self.get_validated_records(use_small_caps_only=use_small_caps_only)]

    def get_exchange_info(self) -> Dict[str, Dict[str, Any]]:
        return {
            exchange: {
                "name": config.get("name", exchange),
                "enabled": config.get("enabled", True),
                "api_source": config.get("api_source", DEFAULT_API_BY_EXCHANGE.get(exchange, "unknown")),
                "ticker_count": len(self.get_exchange_records(exchange)),
            }
            for exchange, config in self.config["exchanges"].items()
        }

    def has_validated_tickers(self) -> bool:
        path = self._snapshot_path(use_small_caps_only=True)
        return bool(path and path.exists())

    def get_validation_info(self) -> Optional[Dict[str, Any]]:
        path = self._snapshot_path(use_small_caps_only=True)
        if not path or not path.exists():
            return None

        payload = self._load_json_file(path, {})
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            return metadata
        return None

    def get_config_issues(self) -> List[str]:
        return list(dict.fromkeys(self._config_issues))


FALLBACK_TICKERS = {
    "US": [
        "BNGO",
        "SNDL",
        "GEVO",
        "CLOV",
        "SENS",
        "MREO",
        "ASTS",
        "IONQ",
        "STEM",
        "QS",
        "LCID",
        "RIVN",
        "SOFI",
        "HOOD",
        "AFRM",
        "OPEN",
    ],
    "TSX": [
        "SHOP.TO",
        "LSPD.TO",
        "BB.TO",
        "HUT.TO",
        "WEED.TO",
        "ACB.TO",
        "TLRY.TO",
        "SNDL.TO",
        "BTO.TO",
        "WDO.TO",
        "LUN.TO",
        "CS.TO",
    ],
    "TSXV": [
        "AMK.V",
        "RECO.V",
        "GIGA.V",
        "DEFN.V",
        "BKMT.V",
        "NVO.V",
        "CBIT.V",
        "CTRL.V",
        "HIVE.V",
        "DMGI.V",
        "BITF.V",
        "HUT.V",
    ],
}


def get_ticker_manager() -> TickerManager:
    return TickerManager()


def get_fallback_tickers(exchange: str) -> List[str]:
    return FALLBACK_TICKERS.get(exchange, [])
