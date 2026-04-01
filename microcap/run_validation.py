#!/usr/bin/env python3
"""
Quick Ticker Validation Runner
Runs ticker validation in the background and provides progress updates.
"""

import sys
import json
from pathlib import Path

def main():
    try:
        # Import validation system
        from validate_tickers import TickerValidator
        
        print("🚀 Starting ticker validation...")
        print("This will validate all tickers and create small-cap focused lists.")
        print("Estimated time: 5-15 minutes depending on number of tickers")
        print("=" * 60)
        
        # Initialize validator
        validator = TickerValidator()
        
        # Show configuration
        config = validator.config
        exchanges = [name for name, cfg in config["exchanges"].items() if cfg.get("enabled", True)]
        print(f"📋 Enabled exchanges: {', '.join(exchanges)}")
        print(f"💰 Small cap threshold: ${config['settings']['max_market_cap_threshold_m']}M")
        print("")
        
        # Confirm before proceeding
        response = input("Continue with validation? (y/n): ").strip().lower()
        if response not in ['y', 'yes']:
            print("Validation cancelled.")
            return
        
        # Run validation
        results = validator.validate_all_exchanges()
        files = validator.save_results(results)
        
        # Show results
        print("\n🎉 Validation Complete!")
        print("=" * 40)
        summary = results['validation_summary']
        print(f"✅ Small caps found: {summary['small_caps_found']}")
        print(f"📊 Total validated: {summary['total_validated']}")  
        print(f"❌ Failed validation: {summary['total_failed']}")
        print(f"⏱️  Duration: {summary['duration_seconds']:.1f} seconds")
        
        print(f"\n📁 Output files:")
        print(f"   🎯 Small caps: {files['small_caps_file']}")
        print(f"   📊 All validated: {files['validated_file']}")
        print(f"   📋 Full report: {files['report_file']}")
        
        print(f"\n🔄 Next steps:")
        print(f"   1. Your app will now use validated small caps automatically")
        print(f"   2. Re-run this script periodically to update the lists")
        print(f"   3. Check the log file: ticker_validation.log")
        
    except KeyboardInterrupt:
        print("\n❌ Validation interrupted")
    except ImportError as e:
        print(f"❌ Error importing validation system: {e}")
        print("Make sure you have all required dependencies installed:")
        print("pip install yfinance requests pandas")
    except Exception as e:
        print(f"❌ Validation failed: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())