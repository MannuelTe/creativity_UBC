# Ticker Management System

## Overview

This project now uses a modern JSON-based ticker management system that replaces the old hardcoded ticker lists. The new system provides:

1. ✅ **JSON-based ticker storage** - Clean, organized ticker files by exchange
2. 🔍 **Ticker validation pipeline** - Automatically validates tickers for existence and data quality
3. 🎯 **Small-cap filtering** - Pre-filters tickers to focus only on actual small caps
4. 🏃 **Improved app performance** - No more random sampling of invalid tickers

## Quick Start

### 1. Run Initial Validation

Before using the app, validate your tickers to create clean, small-cap focused lists:

```bash
python run_validation.py
```

This will:
- Test all tickers for validity (can fetch market data)
- Filter to only include small caps (under your configured threshold)
- Create optimized ticker lists for the app to use

### 2. Use the App

Run your Streamlit app as normal:

```bash
streamlit run app.py
```

The app will now:
- Show a "🎯 Use Validated Small Caps Only" option (recommended)
- Display ticker counts and validation status
- Use pre-validated tickers for much better results

## File Structure

```
microcap/
├── ticker_config.json          # Main configuration
├── tickers_us.json            # US ticker list  
├── tickers_tsx.json           # TSX ticker list
├── tickers_tsxv.json          # TSXV ticker list
├── validate_tickers.py        # Validation script
├── ticker_utils.py            # Ticker management utilities
├── run_validation.py          # Easy validation runner
└── tickers_small_caps_*.json  # Generated validated small caps (after validation)
```

## Configuration

Edit `ticker_config.json` to customize:

```json
{
  "settings": {
    "max_market_cap_threshold_m": 1000,    // Max market cap for small caps ($M)
    "min_market_cap_threshold_m": 1,       // Min market cap ($M) 
    "validation_batch_size": 50,           // Batch size for validation
    "alpha_vantage_rate_limit_per_minute": 5  // API rate limit
  }
}
```

## Adding New Tickers

### Method 1: Edit JSON Files Directly

Add tickers to the appropriate JSON file:
- `tickers_us.json` - US stocks (NASDAQ/NYSE)  
- `tickers_tsx.json` - Toronto Stock Exchange
- `tickers_tsxv.json` - TSX Venture Exchange

### Method 2: Use Custom Input

In the app sidebar, select "Custom tickers" and enter your tickers.

### Method 3: Bulk Import

Create a new JSON file following the same format and add it to `ticker_config.json`.

## Validation Process

The validation system:

1. **Loads tickers** from JSON files by exchange
2. **Tests each ticker** using Yahoo Finance or Alpha Vantage API
3. **Validates data quality** (market cap, price, etc.)
4. **Filters by market cap** to include only small caps
5. **Saves results** in timestamped files

### Validation Results

After validation, you'll have:
- `tickers_small_caps_YYYYMMDD_HHMMSS.json` - Validated small caps (recommended)
- `tickers_validated_YYYYMMDD_HHMMSS.json` - All valid tickers
- `validation_report_YYYYMMDD_HHMMSS.json` - Full validation report
- `ticker_validation.log` - Detailed log file

## Benefits vs Old System

| Old System | New System |
|------------|------------|
| 🔴 2000+ hardcoded tickers | ✅ Organized JSON files |
| 🔴 Many invalid/dead tickers | ✅ Pre-validated tickers |
| 🔴 Random sampling | ✅ Quality-based selection |
| 🔴 No small-cap filtering | ✅ Automatic small-cap focus |
| 🔴 Manual ticker management | ✅ Automated validation pipeline |

## Troubleshooting

### No Valid Tickers Found
- Run `python run_validation.py` first
- Check `ticker_validation.log` for errors
- Verify your API keys are working

### Validation Takes Too Long
- Reduce ticker lists in JSON files
- Adjust `validation_batch_size` in config
- Focus on one exchange at a time

### API Rate Limits
- Validation automatically handles rate limiting
- Alpha Vantage: 5 calls/minute (free tier)  
- Yahoo Finance: Generally unlimited

### App Shows "Using Fallback Lists"
- The JSON files are missing or corrupted
- Run validation to recreate them
- Check file permissions

## Maintenance

### Regular Updates
- Re-run validation monthly to refresh ticker lists
- Remove dead/invalid tickers  
- Add new IPOs and listings

### API Key Management
- Update Alpha Vantage key in `validate_tickers.py` if needed
- Monitor API usage and upgrade if necessary

## Advanced Usage

### Custom Exchanges
Add new exchanges by:
1. Creating a new JSON ticker file
2. Adding exchange config to `ticker_config.json`
3. Updating `ticker_utils.py` if needed

### Validation Customization
Modify `validate_tickers.py` to:
- Add new data validators
- Change market cap thresholds
- Integrate additional APIs

### Integration
Use `TickerManager` class in your own scripts:

```python
from ticker_utils import TickerManager

manager = TickerManager()
small_caps = manager.get_validated_tickers(use_small_caps_only=True)
print(f"Found {len(small_caps)} validated small caps")
```

## Migration from Old System

The old hardcoded ticker lists have been completely replaced. If you have custom modifications to the old lists:

1. Extract your custom tickers
2. Add them to the appropriate JSON files  
3. Run validation to verify they work
4. The app will automatically use the new system

---

🎉 **Enjoy your improved microcap screening with validated, small-cap focused ticker lists!**