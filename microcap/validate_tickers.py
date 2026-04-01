#!/usr/bin/env python3
"""
Ticker Validation and Small Cap Verification System
Validates tickers from JSON files and creates clean, small-cap focused lists.
"""

import json
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set
import pandas as pd
import yfinance as yf
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('ticker_validation.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class TickerValidator:
    """Validates tickers and creates small-cap focused lists"""
    
    def __init__(self, config_file: str = "ticker_config.json"):
        """Initialize with configuration file"""
        self.config_file = Path(config_file)
        self.config = self._load_config()
        self.alpha_vantage_key = "NHEZYAS5ARQ0WVXM"  # Updated API key
        self.validated_tickers = {}
        self.failed_tickers = {}
        
    def _load_config(self) -> Dict:
        """Load configuration from JSON file"""
        try:
            with open(self.config_file, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            logger.error(f"Config file {self.config_file} not found")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in config file: {e}")
            raise
    
    def _load_ticker_file(self, filename: str) -> Dict:
        """Load ticker data from JSON file"""
        try:
            with open(filename, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            logger.error(f"Ticker file {filename} not found")
            return {}
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in ticker file {filename}: {e}")
            return {}
    
    def _validate_yahoo_ticker(self, ticker: str, exchange_suffix: str = "") -> Optional[Dict]:
        """Validate a ticker using Yahoo Finance"""
        full_ticker = f"{ticker}{exchange_suffix}"
        try:
            ticker_obj = yf.Ticker(full_ticker)
            info = ticker_obj.info
            
            # Check if we got meaningful data
            if not info or not isinstance(info, dict):
                return None
                
            market_cap = info.get("marketCap")
            if not market_cap or market_cap <= 0:
                return None
                
            return {
                "ticker": full_ticker,
                "name": info.get("shortName", full_ticker),
                "sector": info.get("sector", "N/A"),
                "industry": info.get("industry", "N/A"),
                "market_cap": market_cap,
                "price": info.get("currentPrice") or info.get("regularMarketPrice", 0),
                "pe_ratio": info.get("trailingPE"),
                "revenue": info.get("totalRevenue", 0),
                "api_source": "yahoo_finance",
                "validated_at": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.debug(f"Yahoo validation failed for {full_ticker}: {e}")
            return None
    
    def _validate_alpha_vantage_ticker(self, ticker: str) -> Optional[Dict]:
        """Validate a ticker using Alpha Vantage API"""
        try:
            # Get company overview
            url = f"https://www.alphavantage.co/query"
            params = {
                "function": "OVERVIEW",
                "symbol": ticker,
                "apikey": self.alpha_vantage_key
            }
            
            response = requests.get(url, params=params, timeout=10)
            if response.status_code != 200:
                return None
                
            data = response.json()
            
            # Check if we got valid data
            if "Symbol" not in data or data.get("Symbol") != ticker.replace('.V', ''):
                return None
                
            market_cap = data.get("MarketCapitalization")
            if not market_cap or market_cap == "None":
                return None
                
            try:
                market_cap = float(market_cap)
            except (ValueError, TypeError):
                return None
                
            if market_cap <= 0:
                return None
            
            return {
                "ticker": ticker,
                "name": data.get("Name", ticker),
                "sector": data.get("Sector", "N/A"),
                "industry": data.get("Industry", "N/A"), 
                "market_cap": market_cap,
                "price": float(data.get("52WeekHigh", 0)) if data.get("52WeekHigh") != "None" else 0,
                "pe_ratio": float(data.get("PERatio", 0)) if data.get("PERatio") not in [None, "None", "-"] else None,
                "revenue": float(data.get("RevenueTTM", 0)) if data.get("RevenueTTM") != "None" else 0,
                "api_source": "alpha_vantage",
                "validated_at": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.debug(f"Alpha Vantage validation failed for {ticker}: {e}")
            return None
    
    def validate_exchange(self, exchange: str) -> Tuple[List[Dict], List[str]]:
        """Validate all tickers for a specific exchange"""
        logger.info(f"Validating tickers for {exchange}")
        
        exchange_config = self.config["exchanges"].get(exchange)
        if not exchange_config or not exchange_config.get("enabled"):
            logger.info(f"Exchange {exchange} is disabled, skipping")
            return [], []
        
        # Load ticker file
        ticker_data = self._load_ticker_file(exchange_config["file"])
        if not ticker_data or "tickers" not in ticker_data:
            logger.error(f"No ticker data found for {exchange}")
            return [], []
        
        tickers = ticker_data["tickers"]
        api_source = exchange_config.get("api_source", "yahoo_finance")
        suffix = ticker_data.get("suffix", "")
        
        logger.info(f"Validating {len(tickers)} tickers from {exchange} using {api_source}")
        
        valid_tickers = []
        failed_tickers = []
        rate_limit_counter = 0
        
        for i, ticker in enumerate(tickers):
            logger.info(f"Validating {ticker} ({i+1}/{len(tickers)})")
            
            if api_source == "alpha_vantage":
                # Rate limiting for Alpha Vantage
                rate_limit_counter += 1
                if rate_limit_counter > 1 and rate_limit_counter % 5 == 1:
                    logger.info("Rate limiting Alpha Vantage API - waiting 12 seconds")
                    time.sleep(12)
                
                result = self._validate_alpha_vantage_ticker(f"{ticker}{suffix}")
            else:
                result = self._validate_yahoo_ticker(ticker, suffix)
            
            if result:
                valid_tickers.append(result)
                logger.info(f"✓ {ticker}: {result['name']} (${result['market_cap']:,.0f} market cap)")
            else:
                failed_tickers.append(ticker)
                logger.warning(f"✗ {ticker}: Validation failed")
            
            # Small delay to be respectful to APIs
            time.sleep(0.1)
        
        logger.info(f"Exchange {exchange} validation complete: {len(valid_tickers)} valid, {len(failed_tickers)} failed")
        return valid_tickers, failed_tickers
    
    def filter_small_caps(self, validated_tickers: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
        """Filter validated tickers to only include small caps"""
        max_cap = self.config["settings"]["max_market_cap_threshold_m"] * 1_000_000
        min_cap = self.config["settings"]["min_market_cap_threshold_m"] * 1_000_000
        
        small_caps = []
        large_caps = []
        
        for ticker_info in validated_tickers:
            market_cap = ticker_info["market_cap"]
            if min_cap <= market_cap <= max_cap:
                small_caps.append(ticker_info)
            else:
                large_caps.append(ticker_info)
        
        logger.info(f"Small cap filtering: {len(small_caps)} small caps, {len(large_caps)} excluded (too large/small)")
        return small_caps, large_caps
    
    def validate_all_exchanges(self) -> Dict:
        """Validate tickers from all enabled exchanges"""
        logger.info("Starting validation for all exchanges")
        start_time = datetime.now()
        
        all_validated = []
        all_failed = {}
        
        for exchange in self.config["exchanges"]:
            valid, failed = self.validate_exchange(exchange)
            all_validated.extend(valid)
            all_failed[exchange] = failed
        
        # Filter to small caps only
        small_caps, excluded = self.filter_small_caps(all_validated)
        
        end_time = datetime.now()
        duration = end_time - start_time
        
        results = {
            "validation_summary": {
                "total_validated": len(all_validated),
                "small_caps_found": len(small_caps),
                "excluded_not_small_cap": len(excluded),
                "total_failed": sum(len(failed) for failed in all_failed.values()),
                "validation_date": start_time.isoformat(),
                "duration_seconds": duration.total_seconds()
            },
            "small_cap_tickers": small_caps,
            "all_validated_tickers": all_validated,
            "failed_tickers": all_failed,
            "excluded_tickers": excluded
        }
        
        logger.info(f"Validation complete in {duration}")
        logger.info(f"Results: {len(small_caps)} small caps found from {len(all_validated)} valid tickers")
        
        return results
    
    def save_results(self, results: Dict):
        """Save validation results to JSON files"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Save small caps (the main output)
        small_caps_file = f"tickers_small_caps_{timestamp}.json"
        with open(small_caps_file, 'w') as f:
            json.dump({
                "metadata": results["validation_summary"],
                "tickers": results["small_cap_tickers"]
            }, f, indent=2)
        logger.info(f"Small cap tickers saved to {small_caps_file}")
        
        # Save all validated tickers
        all_validated_file = f"tickers_validated_{timestamp}.json"
        with open(all_validated_file, 'w') as f:
            json.dump({
                "metadata": results["validation_summary"],
                "tickers": results["all_validated_tickers"]
            }, f, indent=2)
        logger.info(f"All validated tickers saved to {all_validated_file}")
        
        # Save validation report
        report_file = f"validation_report_{timestamp}.json"
        with open(report_file, 'w') as f:
            json.dump(results, f, indent=2)
        logger.info(f"Full validation report saved to {report_file}")
        
        # Update config with latest file references
        self.config["validation"]["last_run"] = results["validation_summary"]["validation_date"]
        self.config["validation"]["small_caps_file"] = small_caps_file
        self.config["validation"]["valid_tickers_file"] = all_validated_file
        
        with open(self.config_file, 'w') as f:
            json.dump(self.config, f, indent=2)
        
        return {
            "small_caps_file": small_caps_file,
            "validated_file": all_validated_file,
            "report_file": report_file
        }


def main():
    """Main validation workflow"""
    print("🔍 Ticker Validation System")
    print("=" * 50)
    
    validator = TickerValidator()
    
    try:
        # Run validation
        results = validator.validate_all_exchanges()
        
        # Save results
        files = validator.save_results(results)
        
        print(f"\n✅ Validation Complete!")
        print(f"📊 Summary:")
        print(f"   - Small caps found: {results['validation_summary']['small_caps_found']}")
        print(f"   - Total validated: {results['validation_summary']['total_validated']}")
        print(f"   - Failed validation: {results['validation_summary']['total_failed']}")
        print(f"   - Duration: {results['validation_summary']['duration_seconds']:.1f} seconds")
        print(f"\n📁 Files created:")
        print(f"   - Small caps: {files['small_caps_file']}")
        print(f"   - All validated: {files['validated_file']}")
        print(f"   - Full report: {files['report_file']}")
        
    except KeyboardInterrupt:
        print("\n❌ Validation interrupted by user")
    except Exception as e:
        logger.error(f"Validation failed: {e}")
        print(f"\n❌ Validation failed: {e}")


if __name__ == "__main__":
    main()