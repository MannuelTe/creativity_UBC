"""
Ticker Management System for Microcap Screener
Loads tickers from JSON files and provides utilities for ticker management.

This module handles file paths to work when launched from creativity_UBC folder.
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

class TickerManager:
    """Manages ticker lists from JSON configuration"""
    
    def __init__(self, config_file: str = "ticker_config.json"):
        """Initialize ticker manager with config file"""
        # Ensure we use the correct path when launched from parent directory
        self.microcap_dir = Path(__file__).parent
        self.config_file = self.microcap_dir / config_file
        self.config = self._load_config()
        self._cached_tickers = {}
    
    def _resolve_file_path(self, filename: str) -> Path:
        """Resolve file path relative to microcap directory"""
        if Path(filename).is_absolute():
            return Path(filename)
        return self.microcap_dir / filename
    
    def _load_config(self) -> Dict:
        """Load ticker configuration"""
        try:
            with open(self.config_file, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            logger.error(f"Config file {self.config_file} not found, using empty config")
            return {"exchanges": {}}
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in config: {e}")
            return {"exchanges": {}}
    
    def _load_ticker_file(self, filename: str) -> List[str]:
        """Load tickers from a JSON file"""
        file_path = self._resolve_file_path(filename)
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
                return data.get("tickers", [])
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Error loading ticker file {file_path}: {e}")
            return []
    
    def get_exchange_tickers(self, exchange: str) -> List[str]:
        """Get tickers for a specific exchange"""
        if exchange in self._cached_tickers:
            return self._cached_tickers[exchange]
        
        exchange_config = self.config["exchanges"].get(exchange)
        if not exchange_config or not exchange_config.get("enabled", True):
            return []
        
        # Load ticker file
        ticker_file = exchange_config.get("file")
        if not ticker_file:
            return []
        
        tickers = self._load_ticker_file(ticker_file)
        
        # Add suffix if specified
        ticker_data = {}
        file_path = self._resolve_file_path(ticker_file)
        try:
            with open(file_path, 'r') as f:
                ticker_data = json.load(f)
        except:
            pass
        
        suffix = ticker_data.get("suffix", "")
        if suffix:
            tickers = [f"{ticker}{suffix}" for ticker in tickers]
        
        self._cached_tickers[exchange] = tickers
        return tickers
    
    def get_all_tickers(self, selected_exchanges: Optional[List[str]] = None) -> List[str]:
        """Get tickers from all selected exchanges"""
        if selected_exchanges is None:
            selected_exchanges = list(self.config["exchanges"].keys())
        
        all_tickers = []
        for exchange in selected_exchanges:
            tickers = self.get_exchange_tickers(exchange)
            all_tickers.extend(tickers)
        
        # Remove duplicates while preserving order
        return list(dict.fromkeys(all_tickers))
    
    def get_validated_tickers(self, use_small_caps_only: bool = True) -> List[str]:
        """Get validated tickers from validation results"""
        if use_small_caps_only:
            filename = self.config.get("validation", {}).get("small_caps_file")
        else:
            filename = self.config.get("validation", {}).get("valid_tickers_file")
        
        if not filename:
            logger.warning(f"Validated ticker file not configured")
            return []
            
        file_path = self._resolve_file_path(filename)
        if not file_path.exists():
            logger.warning(f"Validated ticker file not found: {file_path}")
            return []
        
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
                tickers_data = data.get("tickers", [])
                
                if isinstance(tickers_data, list) and tickers_data:
                    if isinstance(tickers_data[0], dict):
                        # Extract ticker symbols from ticker info objects
                        return [ticker_info["ticker"] for ticker_info in tickers_data]
                    else:
                        # Already a list of ticker symbols
                        return tickers_data
                return []
        except Exception as e:
            logger.error(f"Error loading validated tickers: {e}")
            return []
    
    def get_exchange_info(self) -> Dict:
        """Get information about available exchanges"""
        return {
            exchange: {
                "name": config.get("name", exchange),
                "enabled": config.get("enabled", True),
                "api_source": config.get("api_source", "unknown"),
                "ticker_count": len(self.get_exchange_tickers(exchange))
            }
            for exchange, config in self.config["exchanges"].items()
        }
    
    def has_validated_tickers(self) -> bool:
        """Check if validated ticker files exist"""
        validation_config = self.config.get("validation", {})
        small_caps_file = validation_config.get("small_caps_file")
        if not small_caps_file:
            return False
        file_path = self._resolve_file_path(small_caps_file)
        return file_path.exists()
    
    def get_validation_info(self) -> Optional[Dict]:
        """Get information about last validation run"""
        if not self.has_validated_tickers():
            return None
        
        validation_config = self.config.get("validation", {})
        small_caps_file = validation_config.get("small_caps_file")
        if not small_caps_file:
            return None
            
        file_path = self._resolve_file_path(small_caps_file)
        
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
                return data.get("metadata", {})
        except Exception as e:
            logger.error(f"Error reading validation info: {e}")
            return None


# Legacy fallback tickers for when JSON files aren't available
FALLBACK_TICKERS = {
    "US": [
        "BNGO", "SNDL", "GEVO", "CLOV", "SENS", "MREO", "ASTS", "IONQ", 
        "STEM", "QS", "LCID", "RIVN", "SOFI", "HOOD", "AFRM", "OPEN"
    ],
    "TSX": [
        "SHOP.TO", "LSPD.TO", "BB.TO", "HUT.TO", "WEED.TO", "ACB.TO", 
        "TLRY.TO", "SNDL.TO", "BTO.TO", "WDO.TO", "LUN.TO", "CS.TO"
    ],
    "TSXV": [
        "AMK.V", "RECO.V", "GIGA.V", "DEFN.V", "BKMT.V", "NVO.V",
        "CBIT.V", "CTRL.V", "HIVE.V", "DMGI.V", "BITF.V", "HUT.V"
    ]
}

def get_ticker_manager() -> TickerManager:
    """Get a configured ticker manager instance"""
    return TickerManager()

def get_fallback_tickers(exchange: str) -> List[str]:
    """Get fallback tickers for when JSON system isn't available"""
    return FALLBACK_TICKERS.get(exchange, [])