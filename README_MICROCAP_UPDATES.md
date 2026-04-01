# Microcap Stock Screener - Updated for Parent Directory Launch

This update modifies the Microcap Stock Screener application to be launched from the `creativity_UBC` folder instead of from inside the `microcap` folder.

## What Changed

### 1. **app.py** - Updated Python Path Handling
- **Before**: Added current file directory to Python path
- **After**: Adds `microcap` subdirectory to Python path, allowing launch from parent directory

```python
# Old approach
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

# New approach
microcap_dir = os.path.join(os.getcwd(), 'microcap')
if microcap_dir not in sys.path:
    sys.path.insert(0, microcap_dir)
```

### 2. **ticker_utils.py** - Updated File Path Resolution
- **Before**: Used relative paths for JSON files (could fail when launched from parent directory)
- **After**: Uses absolute path resolution relative to the `microcap` directory

**Key Changes:**
- Added `microcap_dir` property to store the module's directory
- Added `_resolve_file_path()` method for consistent path resolution
- Updated all file operations to use resolved paths
- Fixed `get_validated_tickers()`, `has_validated_tickers()`, and `get_validation_info()` methods

### 3. **New Launcher Script** - `run_microcap_app.py`
- Convenient launcher that can be run from the `creativity_UBC` folder
- Includes error checking and helpful messages
- Automatically finds and launches the app

## How to Use

### Option 1: Use the Launcher Script (Recommended)
```bash
# From creativity_UBC folder
python run_microcap_app.py
```

### Option 2: Use Streamlit Directly  
```bash
# From creativity_UBC folder
streamlit run microcap/app.py
```

### Option 3: Python Module Execution
```bash
# From creativity_UBC folder
python -m streamlit run microcap/app.py
```

## Project Structure
```
creativity_UBC/
├── run_microcap_app.py          # New launcher script
├── microcap/
│   ├── app.py                   # Main application (updated)
│   ├── ticker_utils.py          # Ticker utilities (updated)
│   ├── ticker_config.json       # Configuration file
│   ├── tickers_us.json          # US ticker list
│   ├── tickers_tsx.json         # TSX ticker list
│   ├── tickers_tsxv.json        # TSXV ticker list
│   └── outputs/                 # Generated reports
└── other_files...
```

## Benefits of This Approach

1. **Consistent Working Directory**: The app now works regardless of where it's launched from
2. **Better Organization**: Keeps the microcap project contained in its subfolder
3. **Easier Development**: Can run tests, scripts, or other tools from the parent directory
4. **Path Independence**: All file operations are properly resolved relative to the microcap folder

## Technical Details

The key insight was that the original app used:
- `os.path.dirname(os.path.abspath(__file__))` - which gives the directory of the Python file being executed
- Relative file paths in `ticker_utils.py` that assumed the current working directory was the microcap folder

The updated version:
- Uses `os.path.join(os.getcwd(), 'microcap')` to find the microcap folder relative to current working directory  
- Resolves all file paths relative to the microcap directory using `Path(__file__).parent`
- Maintains backward compatibility if launched from within the microcap folder

## Testing

To verify the changes work correctly:

1. **Test from parent directory:**
   ```bash
   cd /path/to/creativity_UBC
   python run_microcap_app.py
   ```

2. **Test original method still works:**
   ```bash
   cd /path/to/creativity_UBC/microcap  
   streamlit run app.py
   ```

Both approaches should now work correctly with the updated code.