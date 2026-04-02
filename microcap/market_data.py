"""
Shared market-data provider adapters for Yahoo Finance and Alpha Vantage.
"""

from __future__ import annotations

import copy
import logging
import os
import time
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import requests
import yfinance as yf

from ticker_utils import TickerRecord

logger = logging.getLogger(__name__)

_FETCH_CACHE: Dict[tuple[str, str], tuple[float, Dict[str, Any]]] = {}
_LAST_PROVIDER_CALL: Dict[str, float] = {}
_SECRETS_CACHE: Dict[Path, Dict[str, Any]] = {}


def provider_label(provider: str) -> str:
    return {
        "yahoo_finance": "Yahoo Finance",
        "alpha_vantage": "Alpha Vantage",
    }.get(provider, provider)


def load_provider_settings(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    settings = (config or {}).get("settings", {}) if "settings" in (config or {}) else (config or {})
    return {
        "alpha_vantage_rate_limit_per_minute": int(settings.get("alpha_vantage_rate_limit_per_minute", 5) or 5),
        "provider_cache_ttl_seconds": int(settings.get("provider_cache_ttl_seconds", 3600) or 3600),
        "request_retry_count": int(settings.get("request_retry_count", 2) or 2),
        "request_timeout_seconds": int(settings.get("request_timeout_seconds", 20) or 20),
        "retry_backoff_seconds": float(settings.get("retry_backoff_seconds", 2) or 2),
        "yahoo_request_pause_seconds": float(settings.get("yahoo_request_pause_seconds", 0.25) or 0.25),
    }


def _candidate_secret_paths(search_dir: Optional[Path]) -> list[Path]:
    candidates: list[Path] = []
    roots = [search_dir] if search_dir else []
    roots.extend(
        [
            Path(__file__).parent,
            Path(__file__).parent.parent,
        ]
    )

    for root in roots:
        if root is None:
            continue
        root = root.resolve()
        for candidate in (
            root / ".streamlit" / "secrets.toml",
            root.parent / ".streamlit" / "secrets.toml",
        ):
            if candidate not in candidates:
                candidates.append(candidate)

    return candidates


def load_provider_secret(secret_name: str, search_dir: Optional[Path] = None) -> str:
    env_value = os.environ.get(secret_name, "")
    if env_value:
        return env_value

    for secrets_path in _candidate_secret_paths(search_dir):
        if not secrets_path.exists():
            continue

        if secrets_path not in _SECRETS_CACHE:
            try:
                _SECRETS_CACHE[secrets_path] = tomllib.loads(secrets_path.read_text())
            except (OSError, tomllib.TOMLDecodeError):
                _SECRETS_CACHE[secrets_path] = {}

        secret_value = _SECRETS_CACHE[secrets_path].get(secret_name, "")
        if secret_value:
            return str(secret_value)

    return ""


def _parse_float(value: Any) -> Optional[float]:
    if value in (None, "", "None", "N/A", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _failure(provider: str, query_symbol: str, reason: str, message: str, **extra: Any) -> Dict[str, Any]:
    failure = {
        "provider": provider,
        "provider_label": provider_label(provider),
        "query_symbol": query_symbol,
        "reason": reason,
        "message": message,
        "timestamp": datetime.utcnow().isoformat(),
    }
    failure.update({key: value for key, value in extra.items() if value is not None})
    return failure


def format_failure_reason(failure: Dict[str, Any], failures: Optional[list[Dict[str, Any]]] = None) -> str:
    if not failure:
        return "Unknown provider failure"

    message = failure.get("message") or f"{failure.get('provider_label', 'Provider')} failed"
    if failures and len(failures) > 1:
        chain = " -> ".join(
            f"{item.get('provider_label', item.get('provider'))}: {item.get('reason', 'failed')}"
            for item in failures
        )
        return f"{message} ({chain})"
    return message


def _get_cached_result(provider: str, query_symbol: str, settings: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    cache_key = (provider, query_symbol)
    cached = _FETCH_CACHE.get(cache_key)
    if not cached:
        return None

    cached_at, result = cached
    if time.time() - cached_at > settings["provider_cache_ttl_seconds"]:
        _FETCH_CACHE.pop(cache_key, None)
        return None

    return copy.deepcopy(result)


def _set_cached_result(provider: str, query_symbol: str, result: Dict[str, Any]) -> Dict[str, Any]:
    _FETCH_CACHE[(provider, query_symbol)] = (time.time(), copy.deepcopy(result))
    return result


def _throttle_provider(provider: str, settings: Dict[str, Any]) -> None:
    if provider == "alpha_vantage":
        min_interval = 60.0 / max(settings["alpha_vantage_rate_limit_per_minute"], 1)
    else:
        min_interval = settings["yahoo_request_pause_seconds"]

    previous_call = _LAST_PROVIDER_CALL.get(provider)
    if previous_call is not None:
        elapsed = time.time() - previous_call
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)

    _LAST_PROVIDER_CALL[provider] = time.time()


def _calculate_quality_score(market_cap: float, current_price: float, pe_ratio: Optional[float], revenue: float) -> int:
    score = 0
    if market_cap > 0:
        score += 40
    if current_price > 0:
        score += 30
    if pe_ratio and pe_ratio > 0:
        score += 20
    if revenue > 0:
        score += 10
    return score


def _normalize_success_payload(
    record: TickerRecord,
    provider: str,
    name: str,
    sector: str,
    industry: str,
    market_cap: float,
    current_price: float,
    pe_ratio: Optional[float],
    revenue: float,
) -> Dict[str, Any]:
    return {
        "canonical_symbol": record.canonical_symbol,
        "symbol": record.display_symbol,
        "base_symbol": record.base_symbol,
        "name": name or record.company_name or record.display_symbol,
        "company_name": name or record.company_name or record.display_symbol,
        "sector": sector or "N/A",
        "industry": industry or "N/A",
        "market_cap": market_cap,
        "current_price": current_price or 0.0,
        "pe_ratio": pe_ratio,
        "revenue": revenue or 0.0,
        "provider_used": provider,
        "data_source": provider,
        "quality_score": _calculate_quality_score(
            market_cap=market_cap,
            current_price=current_price or 0.0,
            pe_ratio=pe_ratio,
            revenue=revenue or 0.0,
        ),
        "last_fetched_at": datetime.utcnow().isoformat(),
    }


def fetch_yahoo_data(record: TickerRecord, settings: Dict[str, Any]) -> Dict[str, Any]:
    provider = "yahoo_finance"
    query_symbol = record.query_symbols.get("yahoo", record.display_symbol)
    cached = _get_cached_result(provider, query_symbol, settings)
    if cached is not None:
        return cached

    retries = settings["request_retry_count"]
    backoff_seconds = settings["retry_backoff_seconds"]

    for attempt in range(retries):
        try:
            _throttle_provider(provider, settings)
            ticker_obj = yf.Ticker(query_symbol)
            info = ticker_obj.info
            if not info or not isinstance(info, dict) or len(info) < 5:
                result = {
                    "ok": False,
                    "failure": _failure(provider, query_symbol, "not_found", "Yahoo Finance returned no quote data"),
                }
                return _set_cached_result(provider, query_symbol, result)

            market_cap = _parse_float(info.get("marketCap"))
            if not market_cap or market_cap <= 0:
                result = {
                    "ok": False,
                    "failure": _failure(provider, query_symbol, "missing_market_cap", "Yahoo Finance returned no market cap"),
                }
                return _set_cached_result(provider, query_symbol, result)

            current_price = _parse_float(
                info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
            ) or 0.0
            if current_price <= 0:
                history = ticker_obj.history(period="5d")
                if history is not None and not history.empty:
                    current_price = float(history["Close"].iloc[-1])

            pe_ratio = _parse_float(info.get("trailingPE"))
            if pe_ratio is not None and (pe_ratio <= 0 or pe_ratio > 1000):
                pe_ratio = None

            revenue = _parse_float(info.get("totalRevenue")) or 0.0

            result = {
                "ok": True,
                "data": _normalize_success_payload(
                    record=record,
                    provider=provider,
                    name=info.get("shortName") or info.get("longName") or record.company_name,
                    sector=info.get("sector") or "N/A",
                    industry=info.get("industry") or "N/A",
                    market_cap=market_cap,
                    current_price=current_price,
                    pe_ratio=pe_ratio,
                    revenue=revenue,
                ),
            }
            return _set_cached_result(provider, query_symbol, result)
        except Exception as exc:  # noqa: BLE001
            message = str(exc).lower()
            reason = "network_error"
            if "404" in message or "not found" in message or "delisted" in message:
                reason = "not_found"
            elif "json" in message:
                reason = "parse_error"

            failure = _failure(provider, query_symbol, reason, f"Yahoo Finance error: {str(exc)}")
            if attempt < retries - 1 and reason in {"network_error", "parse_error"}:
                time.sleep(backoff_seconds * (attempt + 1))
                continue

            result = {"ok": False, "failure": failure}
            return _set_cached_result(provider, query_symbol, result)

    return _set_cached_result(
        provider,
        query_symbol,
        {"ok": False, "failure": _failure(provider, query_symbol, "network_error", "Yahoo Finance retries exhausted")},
    )


def _fetch_alpha_vantage_quote(symbol: str, api_key: str, settings: Dict[str, Any]) -> tuple[Optional[float], Optional[Dict[str, Any]]]:
    provider = "alpha_vantage"
    _throttle_provider(provider, settings)
    response = requests.get(
        "https://www.alphavantage.co/query",
        params={"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": api_key},
        timeout=settings["request_timeout_seconds"],
    )

    if response.status_code == 429:
        return None, _failure(provider, symbol, "rate_limited", "Alpha Vantage rate limit exceeded")

    try:
        payload = response.json()
    except ValueError:
        return None, _failure(provider, symbol, "parse_error", "Alpha Vantage quote response was not valid JSON")

    if "Note" in payload or "Information" in payload:
        return None, _failure(provider, symbol, "rate_limited", "Alpha Vantage rate limit message received")

    quote = payload.get("Global Quote", {})
    current_price = _parse_float(quote.get("05. price"))
    return current_price, None


def fetch_alpha_vantage_data(
    record: TickerRecord,
    settings: Dict[str, Any],
    search_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    provider = "alpha_vantage"
    query_symbol = record.query_symbols.get("alpha_vantage", record.base_symbol)
    cached = _get_cached_result(provider, query_symbol, settings)
    if cached is not None:
        return cached

    api_key = load_provider_secret("ALPHA_VANTAGE_API_KEY", search_dir=search_dir)
    if not api_key:
        result = {
            "ok": False,
            "failure": _failure(provider, query_symbol, "missing_api_key", "Alpha Vantage API key is not configured"),
        }
        return _set_cached_result(provider, query_symbol, result)

    retries = settings["request_retry_count"]
    backoff_seconds = settings["retry_backoff_seconds"]
    timeout_seconds = settings["request_timeout_seconds"]

    for attempt in range(retries):
        try:
            _throttle_provider(provider, settings)
            response = requests.get(
                "https://www.alphavantage.co/query",
                params={"function": "OVERVIEW", "symbol": query_symbol, "apikey": api_key},
                timeout=timeout_seconds,
            )

            if response.status_code == 429:
                failure = _failure(provider, query_symbol, "rate_limited", "Alpha Vantage rate limit exceeded")
                if attempt < retries - 1:
                    time.sleep(backoff_seconds * (attempt + 1))
                    continue
                result = {"ok": False, "failure": failure}
                return _set_cached_result(provider, query_symbol, result)

            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError:
                failure = _failure(provider, query_symbol, "parse_error", "Alpha Vantage response was not valid JSON")
                if attempt < retries - 1:
                    time.sleep(backoff_seconds * (attempt + 1))
                    continue
                result = {"ok": False, "failure": failure}
                return _set_cached_result(provider, query_symbol, result)

            if "Note" in payload or "Information" in payload:
                failure = _failure(provider, query_symbol, "rate_limited", "Alpha Vantage rate limit message received")
                if attempt < retries - 1:
                    time.sleep(backoff_seconds * (attempt + 1))
                    continue
                result = {"ok": False, "failure": failure}
                return _set_cached_result(provider, query_symbol, result)

            symbol = payload.get("Symbol")
            if not symbol or _normalize_symbol(symbol := str(symbol)) != query_symbol:
                result = {
                    "ok": False,
                    "failure": _failure(provider, query_symbol, "not_found", "Alpha Vantage returned no matching symbol"),
                }
                return _set_cached_result(provider, query_symbol, result)

            market_cap = _parse_float(payload.get("MarketCapitalization"))
            if not market_cap or market_cap <= 0:
                result = {
                    "ok": False,
                    "failure": _failure(provider, query_symbol, "missing_market_cap", "Alpha Vantage returned no market cap"),
                }
                return _set_cached_result(provider, query_symbol, result)

            current_price = _parse_float(payload.get("52WeekHigh")) or 0.0
            if current_price <= 0:
                quote_price, quote_failure = _fetch_alpha_vantage_quote(query_symbol, api_key, settings)
                if quote_failure:
                    logger.debug("Alpha Vantage quote fallback failed for %s: %s", query_symbol, quote_failure["reason"])
                current_price = quote_price or 0.0

            pe_ratio = _parse_float(payload.get("PERatio"))
            if pe_ratio is not None and (pe_ratio <= 0 or pe_ratio > 1000):
                pe_ratio = None

            revenue = _parse_float(payload.get("RevenueTTM")) or 0.0

            result = {
                "ok": True,
                "data": _normalize_success_payload(
                    record=record,
                    provider=provider,
                    name=payload.get("Name") or record.company_name,
                    sector=payload.get("Sector") or "N/A",
                    industry=payload.get("Industry") or "N/A",
                    market_cap=market_cap,
                    current_price=current_price,
                    pe_ratio=pe_ratio,
                    revenue=revenue,
                ),
            }
            return _set_cached_result(provider, query_symbol, result)
        except requests.RequestException as exc:
            failure = _failure(provider, query_symbol, "network_error", f"Alpha Vantage request failed: {str(exc)}")
            if attempt < retries - 1:
                time.sleep(backoff_seconds * (attempt + 1))
                continue
            result = {"ok": False, "failure": failure}
            return _set_cached_result(provider, query_symbol, result)
        except Exception as exc:  # noqa: BLE001
            failure = _failure(provider, query_symbol, "parse_error", f"Alpha Vantage parsing failed: {str(exc)}")
            if attempt < retries - 1:
                time.sleep(backoff_seconds * (attempt + 1))
                continue
            result = {"ok": False, "failure": failure}
            return _set_cached_result(provider, query_symbol, result)

    return _set_cached_result(
        provider,
        query_symbol,
        {"ok": False, "failure": _failure(provider, query_symbol, "network_error", "Alpha Vantage retries exhausted")},
    )


def fetch_market_data_for_record(
    record: TickerRecord,
    settings: Dict[str, Any],
    search_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    providers: list[str] = []
    for provider in (record.preferred_api, *record.fallback_apis):
        if provider and provider not in providers:
            providers.append(provider)

    failures: list[Dict[str, Any]] = []
    for index, provider in enumerate(providers):
        if provider == "yahoo_finance":
            result = fetch_yahoo_data(record, settings)
        elif provider == "alpha_vantage":
            result = fetch_alpha_vantage_data(record, settings, search_dir=search_dir)
        else:
            result = {
                "ok": False,
                "failure": _failure(provider, record.display_symbol, "unsupported_provider", f"Unsupported provider {provider}"),
            }

        if result.get("ok"):
            data = dict(result["data"])
            data["fallback_used"] = index > 0
            data["provider_sequence"] = providers
            return {"ok": True, "data": data, "failures": failures}

        failures.append(result["failure"])

    final_failure = failures[-1] if failures else _failure(
        record.preferred_api,
        record.display_symbol,
        "not_found",
        "No providers were available for this ticker",
    )
    return {"ok": False, "failure": final_failure, "failures": failures}


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()
