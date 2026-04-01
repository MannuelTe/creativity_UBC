"""
Microcap Stock Screener & AI Research Tool
Uses yfinance for stock data and Claude API (with web search) for research.
Supports US (Yahoo Finance) and Canadian (money.tmx.com) data sources.
"""

import os
import streamlit as st
import yfinance as yf
import pandas as pd
import anthropic
import json
import re
import requests
import logging
import warnings
import time
import random
from datetime import datetime
from urllib.error import HTTPError
from requests.exceptions import RequestException, Timeout, ConnectionError
from ticker_utils import TickerManager, get_fallback_tickers

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="Microcap Screener", layout="wide")
st.title("🔬 Microcap Stock Screener & AI Analyst")

# ── Configure logging and warnings ──────────────────────────────────────────
# Suppress yfinance and urllib warnings that show 404 errors to users
warnings.filterwarnings("ignore")
logging.getLogger("yfinance").setLevel(logging.ERROR)
logging.getLogger("urllib3").setLevel(logging.ERROR)
# ── Alpha Vantage API configuration ─────────────────────────────────────────────────────────────
# Get Alpha Vantage API key from secrets
ALPHA_VANTAGE_API_KEY = st.secrets.get("ALPHA_VANTAGE_API_KEY", os.environ.get("ALPHA_VANTAGE_API_KEY", ""))
# ── API Key handling ─────────────────────────────────────────────────────────
# Priority: 1) st.secrets  2) env var  3) sidebar input  4) session state
_api_key = ""
if "ANTHROPIC_API_KEY" in st.secrets:
    _api_key = st.secrets["ANTHROPIC_API_KEY"]
elif os.environ.get("ANTHROPIC_API_KEY"):
    _api_key = os.environ["ANTHROPIC_API_KEY"]

# Use session state to persist API key input
if not _api_key:
    # Initialize session state for API key if not present
    if "anthropic_api_key" not in st.session_state:
        st.session_state.anthropic_api_key = ""
    
    sidebar_key = st.sidebar.text_input(
        "Anthropic API Key",
        type="password",
        value=st.session_state.anthropic_api_key,
        placeholder="sk-ant-api-...",
        help="Required for AI research. Get your key from https://console.anthropic.com/",
        key="api_key_input"
    )
    
    # Update session state
    if sidebar_key:
        st.session_state.anthropic_api_key = sidebar_key
    
    # Use the sidebar input
    _api_key = sidebar_key

# Validate API key format and set final key
api_key_valid = False
validated_api_key = ""

if _api_key:
    if _api_key.startswith("sk-ant-") and len(_api_key) > 20:
        api_key_valid = True
        validated_api_key = _api_key
        os.environ["ANTHROPIC_API_KEY"] = _api_key
        st.sidebar.success("✅ API key validated")
    else:
        st.sidebar.error("⚠️ Invalid API key format. Should start with 'sk-ant-' and be longer than 20 characters")
        validated_api_key = ""  # Don't use invalid key
elif st.session_state.get("anthropic_api_key", ""):
    st.sidebar.info("🔑 Enter a valid API key to enable AI research")
else:
    st.sidebar.info("🔑 Claude API key needed for AI research features")

# ── Industry proxy P/E table ────────────────────────────────────────────────
# Approximate trailing P/E by sector (used when a company has no P/E)
INDUSTRY_PE_PROXY = {
    "Technology":          30.0,
    "Healthcare":          25.0,
    "Financial Services":  12.0,
    "Financials":          12.0,
    "Consumer Cyclical":   18.0,
    "Consumer Defensive":  22.0,
    "Industrials":         20.0,
    "Energy":              10.0,
    "Basic Materials":     14.0,
    "Communication Services": 16.0,
    "Utilities":           18.0,
    "Real Estate":         35.0,
}
DEFAULT_PROXY_PE = 18.0  # fallback if sector not mapped

# ── Sidebar – filters ───────────────────────────────────────────────────────
st.sidebar.header("Screening Filters")

max_cap_m = st.sidebar.slider(
    "Maximum Market Cap ($ millions)",
    min_value=10,
    max_value=1000,
    value=100,
    step=10,
    help="Companies below this market cap are considered microcaps.",
)

data_sources = st.sidebar.multiselect(
    "Data Sources",
    ["Yahoo Finance (US)", "TSX (Canada)", "TSXV (Canada Venture)", "Custom tickers"],
    default=["TSXV (Canada Venture)"],
    help="Select one or more sources. Tickers from all selected sources are combined.",
)

# ── Initialize ticker management system ──────────────────────────────────────
try:
    ticker_manager = TickerManager()
    exchange_info = ticker_manager.get_exchange_info()
    has_validated = ticker_manager.has_validated_tickers()
except Exception as e:
    st.error(f"Error loading ticker management system: {e}")
    ticker_manager = None
    exchange_info = {}
    has_validated = False

# ── Show ticker source options with counts ──────────────────────────────────
if exchange_info:
    st.sidebar.markdown("**Available Ticker Sources:**")
    for exchange, info in exchange_info.items():
        status = "✅" if info['enabled'] else "❌"
        st.sidebar.markdown(f"{status} **{info['name']}**: {info['ticker_count']} tickers")

# Add validated ticker option if available
use_validated = False
if has_validated:
    validation_info = ticker_manager.get_validation_info()
    if validation_info:
        st.sidebar.markdown("---")
        use_validated = st.sidebar.checkbox(
            "🎯 Use Validated Small Caps Only",
            value=True,
            help=f"Use pre-validated small caps from {validation_info.get('validation_date', 'unknown')}. "
                 f"Found {validation_info.get('small_caps_found', 0)} valid small caps."
        )
        if use_validated:
            st.sidebar.success(f"Using {validation_info.get('small_caps_found', 0)} validated small caps")

# ── Ticker selection logic ──────────────────────────────────────────────────
raw_tickers = []

if use_validated and ticker_manager:
    # Use pre-validated small caps
    raw_tickers = ticker_manager.get_validated_tickers(use_small_caps_only=True)
    st.info(f"📋 Using {len(raw_tickers)} pre-validated small cap tickers")
    
elif ticker_manager:
    # Use selected exchange sources
    exchange_mapping = {
        "Yahoo Finance (US)": "US",
        "TSX (Canada)": "TSX", 
        "TSXV (Canada Venture)": "TSXV"
    }
    
    selected_exchanges = [exchange_mapping[source] for source in data_sources 
                         if source in exchange_mapping]
    
    for exchange in selected_exchanges:
        tickers = ticker_manager.get_exchange_tickers(exchange)
        raw_tickers.extend(tickers)
        
    # Add custom tickers if selected
    if "Custom tickers" in data_sources:
        custom_input = st.sidebar.text_area(
            "Enter tickers (comma-separated)",
            placeholder="e.g. AAPL, MSFT, GEVO, SU.TO, AMK.V",
        )
        raw_tickers.extend([t.strip().upper() for t in custom_input.split(",") if t.strip()])
    
    # Deduplicate while preserving order
    raw_tickers = list(dict.fromkeys(raw_tickers))
    
    if raw_tickers:
        st.info(f"📋 Loaded {len(raw_tickers)} tickers from selected sources. Amalgamating")
    else:
        st.warning("No tickers found from selected sources")
        
else:
    # Fallback to basic ticker lists if JSON system fails
    st.warning("⚠️ Using fallback ticker lists - run ticker validation to improve")
    if "Yahoo Finance (US)" in data_sources:
        raw_tickers.extend(get_fallback_tickers("US"))
    if "TSX (Canada)" in data_sources:
        raw_tickers.extend(get_fallback_tickers("TSX"))
    if "TSXV (Canada Venture)" in data_sources:
        raw_tickers.extend(get_fallback_tickers("TSXV"))
    
    raw_tickers = list(dict.fromkeys(raw_tickers))

# ── Enhanced Smart Ticker Sampling & Vetting ─────────────────────────────────
if not raw_tickers:
    st.error("No tickers available. Please select data sources or check ticker configuration.")
    st.stop()

# Smart sampling with implicit selection
auto_select = st.sidebar.checkbox(
    "🎯 Auto-select & Filter (Recommended)", 
    value=True, 
    help="Automatically select 10 random tickers and filter by market cap"
)

if auto_select:
    # Always select 10 random tickers for auto mode
    max_tickers = 10
    st.sidebar.info("Auto mode: Selecting 10 random tickers")
else:
    max_tickers = st.sidebar.number_input(
        "Max tickers to process",
        min_value=1,
        max_value=50,
        value=10,
        help="Higher numbers take longer but give better coverage"
    )

# Enhanced ticker selection with vetting
if len(raw_tickers) > max_tickers:
    if use_validated:
        # For validated lists, take top ones (they're already quality-filtered)
        selected_tickers = raw_tickers[:max_tickers]
        st.info(f"🎯 Processing top {len(selected_tickers)} validated small caps")
    else:
        # Enhanced random sampling with better distribution
        import random
        random.seed(42)  # For reproducible results during session
        selected_tickers = random.sample(raw_tickers, max_tickers)
        if auto_select:
            st.info(f"🎲 Auto-selected {len(selected_tickers)} random tickers for vetting")
else:
    selected_tickers = raw_tickers
    if auto_select:
        st.info(f"📊 Processing all {len(raw_tickers)} available tickers (less than 10 found)")
    else:
        st.info(f"📊 Processing all {len(raw_tickers)} available tickers")

# ── Helpers ──────────────────────────────────────────────────────────────────

def strip_cite_tags(text: str) -> str:
    """Remove <cite index="...">...</cite> wrappers, keeping inner text."""
    return re.sub(r'<cite[^>]*>(.*?)</cite>', r'\1', text)


def clean_research_dict(obj):
    """Recursively strip cite tags from all string values in a dict/list."""
    if isinstance(obj, str):
        return strip_cite_tags(obj)
    if isinstance(obj, dict):
        return {k: clean_research_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean_research_dict(i) for i in obj]
    return obj


def get_proxy_pe(sector: str) -> tuple[float, bool]:
    """Return (pe_value, is_proxy). If sector is mapped, use it; else default."""
    pe = INDUSTRY_PE_PROXY.get(sector, DEFAULT_PROXY_PE)
    return pe, True


def fetch_alpha_vantage_data(ticker: str, retry_count: int = 2) -> dict:
    """Enhanced Alpha Vantage data fetching with robust error handling and strict rate limiting."""
    symbol = ticker.replace('.V', '').replace('.TO', '')
    
    for attempt in range(retry_count):
        try:
            # Alpha Vantage overview endpoint with enhanced error handling
            url = f"https://www.alphavantage.co/query?function=OVERVIEW&symbol={symbol}&apikey={ALPHA_VANTAGE_API_KEY}"
            response = requests.get(url, timeout=20)  # Increased timeout
            
            if response.status_code == 429:  # Rate limit exceeded
                if attempt < retry_count - 1:
                    time.sleep(60)  # Wait full minute before retry on rate limit
                    continue
                return {}
            
            response.raise_for_status()
            data = response.json()
            
            # Enhanced validation with better rate limit detection
            if not data or 'Symbol' not in data or data.get('Symbol') == 'None':
                if 'Note' in data and 'API call frequency' in data.get('Note', ''):
                    # Rate limit detected in response
                    if attempt < retry_count - 1:
                        time.sleep(60)  # Wait full minute on rate limit message
                        continue
                elif 'Information' in data and 'API call frequency' in data.get('Information', ''):
                    # Another rate limit message format
                    if attempt < retry_count - 1:
                        time.sleep(60)
                        continue
                return {}
            
            # Validate market cap early
            market_cap = 0
            market_cap_str = data.get('MarketCapitalization', '0')
            if market_cap_str and market_cap_str not in ['None', '0', 'N/A']:
                try:
                    market_cap = float(market_cap_str)
                except (ValueError, TypeError):
                    market_cap = 0
            
            if market_cap <= 0:
                return {}  # Skip if no valid market cap
            
            # Get current price with fallback methods (skip separate quote call to reduce API usage)
            current_price = 0
            try:
                # Use 52-week high from overview data to avoid extra API call
                high_str = data.get('52WeekHigh', '0')
                if high_str and high_str not in ['None', '0', 'N/A']:
                    current_price = float(high_str)
                else:
                    # Only make quote call if absolutely necessary
                    time.sleep(1)  # Small delay between API calls
                    quote_url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={ALPHA_VANTAGE_API_KEY}"
                    quote_response = requests.get(quote_url, timeout=15)
                    if quote_response.status_code == 200:
                        quote_data = quote_response.json()
                        if 'Global Quote' in quote_data and '05. price' in quote_data['Global Quote']:
                            price_str = quote_data['Global Quote']['05. price']
                            current_price = float(price_str) if price_str not in ['None', '0'] else 0
            except Exception:
                current_price = 0
            
            # Enhanced P/E extraction
            pe_ratio = None
            pe_str = data.get('PERatio', 'None')
            if pe_str and pe_str not in ['None', '-', 'N/A']:
                try:
                    pe_ratio = float(pe_str)
                    if pe_ratio <= 0 or pe_ratio > 1000:  # Sanity check
                        pe_ratio = None
                except (ValueError, TypeError):
                    pe_ratio = None
            
            # Enhanced revenue extraction
            revenue = 0
            revenue_str = data.get('RevenueTTM', '0')
            if revenue_str and revenue_str != 'None':
                try:
                    revenue = float(revenue_str)
                except (ValueError, TypeError):
                    revenue = 0
            
            return {
                'symbol': ticker,
                'name': data.get('Name', ticker),
                'sector': data.get('Sector', 'N/A'),
                'industry': data.get('Industry', 'N/A'),
                'market_cap': market_cap,
                'current_price': current_price,
                'pe_ratio': pe_ratio,
                'revenue': revenue,
                'data_source': 'alpha_vantage',
                'validation_score': _calculate_data_quality_score({
                    'market_cap': market_cap, 'price': current_price, 
                    'pe_ratio': pe_ratio, 'revenue': revenue
                })
            }
            
        except requests.RequestException as e:
            if attempt < retry_count - 1:
                time.sleep(5 * (attempt + 1))  # Progressive backoff
                continue
        except Exception as e:
            if attempt < retry_count - 1:
                time.sleep(2)
                continue
    
    return {}

def _calculate_data_quality_score(data: dict) -> int:
    """Calculate a data quality score (0-100) based on available fields."""
    score = 0
    if data.get('market_cap', 0) > 0: score += 40
    if data.get('price', 0) > 0: score += 30
    if data.get('pe_ratio'): score += 20
    if data.get('revenue', 0) > 0: score += 10
    return score


def fetch_yahoo_data_robust(ticker: str, retry_count: int = 2) -> dict:
    """Enhanced Yahoo Finance data fetching with robust validation."""
    for attempt in range(retry_count):
        try:
            ticker_obj = yf.Ticker(ticker)
            info = ticker_obj.info
            
            # Enhanced validation
            if not info or not isinstance(info, dict) or len(info) < 5:
                if attempt < retry_count - 1:
                    time.sleep(1)
                    continue
                return {}
            
            # Validate market cap early
            market_cap = info.get("marketCap")
            if not market_cap or market_cap <= 0:
                return {}
            
            # Enhanced price extraction
            price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose") or 0
            if price <= 0:
                # Try historical data as fallback
                try:
                    hist = ticker_obj.history(period="1d")
                    if not hist.empty:
                        price = hist['Close'].iloc[-1]
                except:
                    price = 0
            
            # Enhanced sector/industry validation
            sector = info.get("sector", "N/A")
            industry = info.get("industry", "N/A")
            if sector == "N/A" and industry == "N/A":
                # Lower quality score for missing sector info
                pass
            
            return {
                'symbol': ticker,
                'name': info.get("shortName", info.get("longName", ticker)),
                'sector': sector,
                'industry': industry,
                'market_cap': market_cap,
                'current_price': price,
                'pe_ratio': info.get("trailingPE"),
                'revenue': info.get("totalRevenue", 0),
                'data_source': 'yahoo_finance',
                'validation_score': _calculate_data_quality_score({
                    'market_cap': market_cap, 'price': price,
                    'pe_ratio': info.get("trailingPE"), 'revenue': info.get("totalRevenue", 0)
                })
            }
            
        except Exception as e:
            if attempt < retry_count - 1:
                time.sleep(1)
                continue
    
    return {}

def fetch_market_caps_live(tickers: list[str]) -> pd.DataFrame:
    """Enhanced market cap fetching with robust vetting and automatic filtering.
    Updates display live and filters results by market cap threshold.
    """
    rows = []
    failed_tickers = []
    av_count = 0  # Track Alpha Vantage API calls
    quality_scores = []
    
    # Create placeholders for live updates
    progress_bar = st.progress(0)
    status_text = st.empty()
    df_placeholder = st.empty()
    vetting_info = st.empty()
    
    total_tickers = len(tickers)
    
    # Show vetting process info
    vetting_info.info(f"🔍 **Vetting Process Started**: Validating {total_tickers} tickers with market cap filter ≤ ${max_cap_m}M")
    
    for i, t in enumerate(tickers):
        # Update progress
        progress = (i + 1) / total_tickers
        progress_bar.progress(progress)
        status_text.text(f"🔍 Vetting {t} ({i+1}/{total_tickers})...")
        
        try:
            ticker_data = None
            api_used = None
            
            # Enhanced ticker routing with better fallback logic
            if t.endswith('.V') or t.endswith('.TO'):  # Canadian stocks
                av_count += 1
                # Enhanced rate limiting for Alpha Vantage (5 calls per minute)
                if av_count > 1 and av_count % 5 == 1:
                    status_text.text(f"⏱️ Rate limiting Alpha Vantage: waiting 12s... ({i+1}/{total_tickers})")
                    time.sleep(12)
                elif av_count > 1:  # Add small delay between all calls
                    time.sleep(0.5)
                
                ticker_data = fetch_alpha_vantage_data(t)
                api_used = "Alpha Vantage"
                
                # Fallback to Yahoo if Alpha Vantage fails
                if not ticker_data:
                    status_text.text(f"🔄 Fallback: trying Yahoo Finance for {t}... ({i+1}/{total_tickers})")
                    ticker_data = fetch_yahoo_data_robust(t)
                    api_used = "Yahoo Finance (fallback)"
                    
            else:  # US/TSX stocks - use Yahoo Finance primarily
                ticker_data = fetch_yahoo_data_robust(t)
                api_used = "Yahoo Finance"
            
            # Skip if no data or failed validation
            if not ticker_data:
                failed_tickers.append((t, f"No valid data from {api_used}"))
                continue
            
            # Market cap filtering (convert to millions for comparison)
            market_cap_m = ticker_data.get('market_cap', 0) / 1e6
            if market_cap_m > max_cap_m:
                failed_tickers.append((t, f"Market cap ${market_cap_m:.1f}M exceeds ${max_cap_m}M threshold"))
                continue
            
            # Extract and validate data
            sector = ticker_data.get('sector', 'N/A')
            pe_ratio = ticker_data.get('pe_ratio')
            
            # P/E handling with proxy fallback
            if pe_ratio and pe_ratio > 0 and pe_ratio < 1000:  # Sanity check
                pe_display = round(pe_ratio, 1)
                pe_is_proxy = False
            else:
                proxy_pe, _ = get_proxy_pe(sector)
                pe_display = proxy_pe
                pe_is_proxy = True
            
            quality_score = ticker_data.get('validation_score', 0)
            quality_scores.append(quality_score)
            
            new_row = {
                "Ticker": t,
                "Company": ticker_data.get('name', t),
                "Sector": sector,
                "Industry": ticker_data.get('industry', 'N/A'),
                "Market Cap ($M)": round(market_cap_m, 1),
                "Price ($)": round(ticker_data.get('current_price', 0), 2),
                "P/E": pe_display,
                "P/E Source": "Proxy (industry avg)" if pe_is_proxy else "Reported",
                "Revenue ($M)": round(ticker_data.get('revenue', 0) / 1e6, 1),
                "Data Source": api_used,
                "Quality Score": quality_score
            }
            rows.append(new_row)
            
            # Live update of results table
            if rows:
                temp_df = pd.DataFrame(rows)
                df_placeholder.dataframe(
                    temp_df[["Ticker", "Company", "Market Cap ($M)", "Price ($)", "Sector", "Quality Score"]], 
                    use_container_width=True
                )
                
        except Exception as e:
            failed_tickers.append((t, f"Unexpected error: {str(e)[:50]}..."))
            continue
    
    # Clear progress indicators
    progress_bar.empty()
    status_text.empty()
    df_placeholder.empty()
    
    # Generate comprehensive results summary
    total_processed = len(rows) + len(failed_tickers)
    avg_quality = sum(quality_scores) / len(quality_scores) if quality_scores else 0
    
    if rows:
        df = pd.DataFrame(rows)
        # Sort by market cap (ascending) to show smallest caps first
        df = df.sort_values('Market Cap ($M)')
        
        # Enhanced results summary
        st.success(f"✅ **Vetting Complete!** Found {len(rows)} valid microcaps from {total_processed} tickers processed")
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("✅ Valid Tickers", len(rows))
        with col2:
            st.metric("❌ Filtered Out", len(failed_tickers))
        with col3:
            st.metric("🎯 Success Rate", f"{len(rows)/total_processed*100:.1f}%")
        with col4:
            st.metric("📈 Avg Quality Score", f"{avg_quality:.0f}/100")
        
        # Show market cap distribution
        if len(rows) > 1:
            market_caps = [row['Market Cap ($M)'] for row in rows]
            st.info(f"💰 **Market Cap Range**: ${min(market_caps):.1f}M - ${max(market_caps):.1f}M (median: ${sorted(market_caps)[len(market_caps)//2]:.1f}M)")
        
        # Show AlphaVantage usage summary
        if av_count > 0:
            st.info(f"🔌 **API Usage**: {av_count} AlphaVantage calls made (rate limited to 5/minute)")
    
    else:
        st.error(f"❌ No valid tickers found from {total_processed} processed. All tickers filtered out.")
        df = pd.DataFrame()  # Return empty DataFrame
    
    # Show detailed failure reasons if any
    if failed_tickers:
        with st.expander(f"🔍 View {len(failed_tickers)} filtered/failed tickers", expanded=False):
            failure_df = pd.DataFrame(failed_tickers, columns=["Ticker", "Reason"])
            st.dataframe(failure_df, use_container_width=True)
    
    vetting_info.empty()
    return df


# ── Step 1: Screen ──────────────────────────────────────────────────────────
st.header("1 · Screen for Microcaps")

if not selected_tickers:
    st.info("Select at least one data source in the sidebar (or add custom tickers).")
    st.stop()

source_label = ", ".join(data_sources) if data_sources else "None"

# Check if we already have screening results in session state
if "df_filtered" not in st.session_state or "last_screening_params" not in st.session_state or st.session_state["last_screening_params"] != (tuple(selected_tickers), max_cap_m, tuple(data_sources)):
    # Need to run screening
    with st.expander("🔍 Live Screening Progress", expanded=True):
        st.write(f"**Fetching market data from {source_label}...**")
        df_all = fetch_market_caps_live(selected_tickers)
    
    if df_all.empty:
        st.warning("No data returned. Check your tickers or internet connection.")
        st.stop()
    
    # Apply market cap filter and store results
    df_filtered = df_all[df_all["Market Cap ($M)"] <= max_cap_m].copy()
    
    # Store in session state
    st.session_state["df_all"] = df_all
    st.session_state["df_filtered"] = df_filtered
    st.session_state["last_screening_params"] = (tuple(selected_tickers), max_cap_m, tuple(data_sources))
else:
    # Use cached results
    df_all = st.session_state["df_all"]
    df_filtered = st.session_state["df_filtered"]
    st.success(f"✅ Using cached screening results from {source_label}")

st.write(f"**{len(df_filtered)}** companies under **${max_cap_m}M** market cap (from {len(df_all)} fetched)")

if df_filtered.empty:
    st.info("No companies match your filter. Try raising the market-cap slider.")
    st.stop()

# Display final filtered dataframe with styling
def style_pe(row):
    if row["P/E Source"] == "Proxy (industry avg)":
        return [""] * (len(row) - 2) + ["background-color: #fff3cd"] * 2
    return [""] * len(row)

with st.expander("📊 Screening Results", expanded=False):
    st.dataframe(
        df_filtered.style.apply(style_pe, axis=1),
        width='stretch',
        hide_index=True,
    )
    st.caption("Rows highlighted in yellow use an **industry-average proxy P/E** because the company has no reported trailing P/E (e.g. pre-revenue or negative earnings).")


# ── One-liner descriptions via single Claude call ────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_descriptions(tickers_and_names: tuple[tuple[str, str], ...], api_key: str) -> dict[str, str]:
    """One API call to get a short description for each company. Returns {ticker: description}."""
    if not api_key:
        return {}
    lines = "\n".join(f"- {t}: {n}" for t, n in tickers_and_names)
    prompt = f"""For each company below, write ONE short sentence (max 12 words) describing what it does.
Return JSON only: {{"TICKER": "description", ...}}. No HTML tags.

{lines}"""
    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text
        text = re.sub(r'</?[a-zA-Z][^>]*>', '', strip_cite_tags(text))
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            return json.loads(match.group())
    except Exception:
        pass
    return {}

# Fetch one-liner descriptions
ticker_name_pairs = tuple(
    (row["Ticker"], row["Company"])
    for _, row in df_filtered.iterrows()
)
# Add descriptions if not already added
if "Description" not in df_filtered.columns:
    if api_key_valid and len(ticker_name_pairs) > 0:
        with st.spinner("Generating company descriptions …"):
            descs = fetch_descriptions(ticker_name_pairs, validated_api_key)
        df_filtered.insert(2, "Description", df_filtered["Ticker"].map(descs).fillna(""))
        # Update session state
        st.session_state["df_filtered"] = df_filtered
    else:
        df_filtered.insert(2, "Description", "")
        st.session_state["df_filtered"] = df_filtered

# ── Step 2: Select & Research ────────────────────────────────────────────────
st.header("2 · AI-Powered Deep Dive")

selected_ticker = st.selectbox(
    "Select a company to research",
    options=df_filtered["Ticker"].tolist(),
    format_func=lambda t: f"{t} — {df_filtered.loc[df_filtered['Ticker']==t, 'Company'].values[0]}",
)

sel_row = df_filtered[df_filtered["Ticker"] == selected_ticker].iloc[0]

col1, col2, col3, col4 = st.columns(4)
col1.metric("Price", f"${sel_row['Price ($)']}")
col2.metric("Market Cap", f"${sel_row['Market Cap ($M)']}M")
pe_label = f"{sel_row['P/E']}"
if sel_row["P/E Source"] == "Proxy (industry avg)":
    pe_label += " *"
col3.metric("P/E", pe_label, help="* = industry proxy" if sel_row["P/E Source"] == "Proxy (industry avg)" else None)
col4.metric("Revenue", f"${sel_row['Revenue ($M)']}M")

if sel_row["P/E Source"] == "Proxy (industry avg)":
    st.caption(f"\\* P/E is an industry proxy for the **{sel_row['Sector']}** sector — company has no reported trailing P/E.")

# ── Data Aggregation Methodology ─────────────────────────────────────────────
with st.expander("ℹ️  How is this data aggregated?", expanded=False):
    st.markdown("""
#### Data Pipeline Overview

This tool combines **multiple data sources** to build each research report:

| Stage | Source | What it provides | Freshness |
|-------|--------|-----------------|-----------|  
| **1a. US Screening** | Yahoo Finance API | Market cap, price, P/E, revenue, sector, industry | Real-time (cached 1 hr) |
| **1b. Canadian Screening** | Alpha Vantage API | Market cap, price, P/E, revenue, sector, industry | Real-time (cached 1 hr) |
| **2. AI Research + News** | Claude API + live web search (up to 5 queries) | Multiples, growth, management, catalysts, verdict, local news, developments | Live at query time |

#### Step-by-step flow

1. **Ticker list** is sourced from curated seed lists (US/Canadian) or your custom input.
2. **Multi-API approach**:
   - **Yahoo Finance**: US stocks, TSX (.TO) stocks 
   - **Alpha Vantage**: TSXV (.V) venture exchange stocks
3. **Data validation**: Tickers with no market cap data are dropped.
4. **P/E fallback**: If a company has no trailing P/E (pre-revenue, negative earnings), the tool substitutes an **industry-average proxy P/E** and flags it in the table.
5. **Market cap filter** (your slider) narrows the list to microcap territory.
6. When you click **"Research"**, a **single Claude API call** runs with up to **5 web searches**, covering multiples, growth, management, catalysts, news, and verdict in one pass.

#### Limitations
- **Curated ticker lists** are not exhaustive — use "Custom tickers" for specific companies.
- **Multi-API approach**: Yahoo Finance for US/TSX stocks, Alpha Vantage for TSXV (.V) stocks.
- **Alpha Vantage rate limits**: Free tier allows 5 calls/minute, 500 calls/day.
- **AI research** reflects what is publicly available on the web at query time; it may miss very recent filings or paywalled content.
- **Proxy P/E** is a rough sector average and does not account for sub-industry variation.
""")

# ── Claude research function ────────────────────────────────────────────────

def _parse_json_response(text: str) -> dict:
    """Try to parse JSON from Claude's text response."""
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                return {"raw_response": text}
        return {"raw_response": text}


def run_combined_research(ticker: str, company_name: str, sector: str, industry: str, market_cap: float) -> tuple[dict, dict]:
    """Single Claude API call for both research and news. Returns (research, news)."""
    
    # Validate API key before creating client
    if not validated_api_key:
        raise ValueError("Valid Anthropic API key required")

    client = anthropic.Anthropic(api_key=validated_api_key)

    prompt = f"""Research **{company_name}** ({ticker}), {industry} sector, ~${market_cap}M market cap.
Return a single JSON object (no markdown, no HTML tags, plain text only, no <cite> tags):

{{
  "multiples": {{"pe_ratio":"","ps_ratio":"","pb_ratio":"","ev_ebitda":"","comparison_to_peers":"1 sentence"}},
  "growth_expectations": {{"industry_outlook":"1 sentence","company_growth":"1 sentence","analyst_consensus":"1 sentence"}},
  "management_and_news": {{"key_executives":"CEO + 1-2 others","recent_news":"3 bullet items","local_market_context":"1 sentence"}},
  "pending_catalysts": {{"upcoming_events":"key dates","potential_upside":"1 sentence","potential_risks":"1 sentence"}},
  "verdict": {{"classification":"UNDERVALUED|FAIR VALUE|MOMENTUM PLAY","confidence":"LOW|MEDIUM|HIGH","reasoning":"2 sentences","price_context":"1 sentence"}},
  "local_news": [{{"date":"YYYY-MM-DD","headline":"","summary":"1 sentence","source":"","sentiment":"positive|negative|neutral"}}],
  "recent_developments": [{{"date":"YYYY-MM-DD","category":"earnings|partnership|product|regulatory|other","title":"","detail":"1 sentence","impact":"bullish|bearish|neutral"}}],
  "news_sentiment_summary": "1 sentence"
}}

Keep values concise. 3 news items max, 2 developments max. No HTML."""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
        messages=[{"role": "user", "content": prompt}],
    )

    text_parts = [b.text for b in response.content if b.type == "text"]
    full_text = "\n".join(text_parts)
    full_text = strip_cite_tags(full_text)
    full_text = re.sub(r'</?[a-zA-Z][^>]*>', '', full_text)

    combined = _parse_json_response(full_text)
    combined = clean_research_dict(combined)

    # Split into research and news dicts
    news_keys = {"local_news", "recent_developments", "news_sentiment_summary"}
    news = {k: combined.pop(k) for k in news_keys if k in combined}
    if "raw_response" in combined:
        news = {"raw_response": combined["raw_response"]}

    return combined, news


# ── Research form (prevents page reload) ────────────────────────────────────

if not api_key_valid:
    st.warning("🔑 **Enter your Anthropic API key in the sidebar to enable AI research.**")
    st.info("💡 **Get your API key**: Visit https://console.anthropic.com/ to get your Claude API key (starts with 'sk-ant-')")

with st.form(key="research_form"):
    research_button = st.form_submit_button(
        "🔍 Research this Company with AI", 
        type="primary", 
        use_container_width=True, 
        disabled=not api_key_valid
    )
    
    if research_button and api_key_valid:
        # Debug: Check if we actually have the validated key
        if not validated_api_key:
            st.error("🐛 Debug: API key validation passed but validated_api_key is empty. Please refresh the page.")
            st.stop()
            
        with st.spinner(f"Researching {sel_row['Company']} (single API call, ~5 web searches) …"):
            try:
                research, news = run_combined_research(
                    ticker=selected_ticker,
                    company_name=sel_row["Company"],
                    sector=sel_row["Sector"],
                    industry=sel_row["Industry"],
                    market_cap=sel_row["Market Cap ($M)"],
                )
                st.session_state["research"] = research
                st.session_state["news"] = news
                st.session_state["researched_ticker"] = selected_ticker
                st.session_state["researched_name"] = sel_row["Company"]
                st.success(f"✅ Research completed for {sel_row['Company']}!")

            except anthropic.APIError as e:
                if "authentication" in str(e).lower():
                    st.error("🔑 **Authentication Error**: The API key failed to authenticate with Anthropic.")
                    st.error(f"**Debug Info**: API key length: {len(validated_api_key) if validated_api_key else 0}, starts with sk-ant-: {validated_api_key.startswith('sk-ant-') if validated_api_key else False}")
                    st.info("💡 **Try**: Re-enter your API key in the sidebar or get a new one from: https://console.anthropic.com/")
                else:
                    st.error(f"Claude API error: {e}")
            except ValueError as e:
                st.error(f"⚠️ Validation Error: {e}")
                st.error(f"**Debug Info**: validated_api_key exists: {bool(validated_api_key)}")
            except Exception as e:
                st.error(f"Unexpected error: {e}")

# ── Display results ──────────────────────────────────────────────────────────

if "research" in st.session_state:
    research = st.session_state["research"]
    ticker = st.session_state["researched_ticker"]
    name = st.session_state["researched_name"]

    st.divider()
    st.header(f"3 · Research Results: {name} ({ticker})")

    if "raw_response" in research:
        st.markdown(research["raw_response"])
    else:
        # ── Multiples ────────────────────────────────────────────────────
        with st.expander("📊 Valuation Multiples", expanded=True):
            m = research.get("multiples", {})
            mc1, mc2, mc3, mc4 = st.columns(4)
            mc1.metric("P/E", m.get("pe_ratio", "N/A"))
            mc2.metric("P/S", m.get("ps_ratio", "N/A"))
            mc3.metric("P/B", m.get("pb_ratio", "N/A"))
            mc4.metric("EV/EBITDA", m.get("ev_ebitda", "N/A"))
            st.markdown(f"**Peer Comparison:** {m.get('comparison_to_peers', 'N/A')}")

        # ── Growth ───────────────────────────────────────────────────────
        with st.expander("📈 Growth Expectations", expanded=True):
            g = research.get("growth_expectations", {})
            st.markdown(f"**Industry Outlook:** {g.get('industry_outlook', 'N/A')}")
            st.markdown(f"**Company Growth:** {g.get('company_growth', 'N/A')}")
            st.markdown(f"**Analyst Consensus:** {g.get('analyst_consensus', 'N/A')}")

        # ── Management & News ────────────────────────────────────────────
        with st.expander("👥 Management & News", expanded=True):
            mn = research.get("management_and_news", {})
            st.markdown(f"**Key Executives:** {mn.get('key_executives', 'N/A')}")
            st.markdown(f"**Recent News:** {mn.get('recent_news', 'N/A')}")
            st.markdown(f"**Local Context:** {mn.get('local_market_context', 'N/A')}")

        # ── Local News & Recent Developments ────────────────────────────
        news = st.session_state.get("news", {})
        if "raw_response" not in news:
            with st.expander("📰 Local News & Recent Developments", expanded=True):
                # Sentiment summary
                sentiment_summary = news.get("news_sentiment_summary", "")
                if sentiment_summary:
                    st.info(f"**Overall Sentiment:** {sentiment_summary}")

                # News items table
                news_items = news.get("local_news", [])
                if news_items and isinstance(news_items, list):
                    st.subheader("Recent News", divider="gray")
                    for item in news_items:
                        if not isinstance(item, dict):
                            continue
                        sentiment = item.get("sentiment", "neutral")
                        sent_icon = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}.get(sentiment, "⚪")
                        date_str = item.get("date", "N/A")
                        source = item.get("source", "")
                        source_tag = f" — *{source}*" if source else ""
                        st.markdown(f"{sent_icon} **{item.get('headline', 'N/A')}**  \n"
                                    f"  {date_str}{source_tag}  \n"
                                    f"  {item.get('summary', '')}")

                if not news_items:
                    st.caption("No recent news articles found — this is common for very small microcaps and may itself be a signal (low coverage).")

                # Developments
                devs = news.get("recent_developments", [])
                if devs and isinstance(devs, list):
                    st.subheader("Corporate Developments", divider="gray")
                    for dev in devs:
                        if not isinstance(dev, dict):
                            continue
                        impact = dev.get("impact", "neutral")
                        imp_icon = {"bullish": "📈", "bearish": "📉", "neutral": "➡️"}.get(impact, "➡️")
                        cat = dev.get("category", "other").upper()
                        st.markdown(f"{imp_icon} **[{cat}]** {dev.get('title', 'N/A')}  \n"
                                    f"  {dev.get('date', 'N/A')}  \n"
                                    f"  {dev.get('detail', '')}")
        else:
            with st.expander("📰 Local News & Recent Developments", expanded=True):
                st.markdown(news.get("raw_response", "No news data available."))

        # ── Catalysts ────────────────────────────────────────────────────
        with st.expander("🚀 Pending Catalysts", expanded=True):
            c = research.get("pending_catalysts", {})
            st.markdown(f"**Upcoming Events:** {c.get('upcoming_events', 'N/A')}")
            st.markdown(f"**Potential Upside:** {c.get('potential_upside', 'N/A')}")
            st.markdown(f"**Potential Risks:** {c.get('potential_risks', 'N/A')}")

        # ── Verdict ──────────────────────────────────────────────────────
        st.divider()
        v = research.get("verdict", {})
        classification = v.get("classification", "N/A")
        confidence = v.get("confidence", "N/A")

        color_map = {
            "UNDERVALUED": "🟢",
            "FAIR VALUE": "🟡",
            "MOMENTUM PLAY": "🔵",
        }
        icon = color_map.get(classification, "⚪")

        st.header(f"4 · Verdict: {icon} {classification}")
        st.markdown(f"**Confidence:** {confidence}")
        st.markdown(f"**Reasoning:** {v.get('reasoning', 'N/A')}")
        st.markdown(f"**Price Context:** {v.get('price_context', 'N/A')}")

    # ── Download button ──────────────────────────────────────────────────
    st.divider()
    report_date = datetime.now().strftime("%Y-%m-%d %H:%M")

    report_text = f"""MICROCAP RESEARCH REPORT
{'='*60}
Company: {name} ({ticker})
Date: {report_date}
{'='*60}

"""
    if "raw_response" in research:
        report_text += research["raw_response"]
    else:
        clean_report = f"""MICROCAP RESEARCH REPORT
{'='*60}
Company: {name} ({ticker})
Date: {report_date}
Data Source: {source_label}
{'='*60}

VALUATION MULTIPLES
{'-'*40}
P/E Ratio: {research.get('multiples',{}).get('pe_ratio','N/A')}
P/S Ratio: {research.get('multiples',{}).get('ps_ratio','N/A')}
P/B Ratio: {research.get('multiples',{}).get('pb_ratio','N/A')}
EV/EBITDA: {research.get('multiples',{}).get('ev_ebitda','N/A')}
Peer Comparison: {research.get('multiples',{}).get('comparison_to_peers','N/A')}

GROWTH EXPECTATIONS
{'-'*40}
Industry Outlook: {research.get('growth_expectations',{}).get('industry_outlook','N/A')}
Company Growth: {research.get('growth_expectations',{}).get('company_growth','N/A')}
Analyst Consensus: {research.get('growth_expectations',{}).get('analyst_consensus','N/A')}

MANAGEMENT & NEWS
{'-'*40}
Key Executives: {research.get('management_and_news',{}).get('key_executives','N/A')}
Recent News: {research.get('management_and_news',{}).get('recent_news','N/A')}
Local Context: {research.get('management_and_news',{}).get('local_market_context','N/A')}

LOCAL NEWS & RECENT DEVELOPMENTS
{'-'*40}
"""
        # Build news section for download
        dl_news = st.session_state.get("news", {})
        if "raw_response" not in dl_news:
            dl_sentiment = dl_news.get("news_sentiment_summary", "N/A")
            clean_report += f"Sentiment Summary: {dl_sentiment}\n\n"
            for i, item in enumerate(dl_news.get("local_news", []), 1):
                if isinstance(item, dict):
                    clean_report += (f"  [{item.get('sentiment','?').upper()}] {item.get('date','N/A')} - "
                                     f"{item.get('headline','N/A')} ({item.get('source','N/A')})\n"
                                     f"    {item.get('summary','')}\n\n")
            for i, dev in enumerate(dl_news.get("recent_developments", []), 1):
                if isinstance(dev, dict):
                    clean_report += (f"  [{dev.get('impact','?').upper()}] [{dev.get('category','other').upper()}] "
                                     f"{dev.get('date','N/A')} - {dev.get('title','N/A')}\n"
                                     f"    {dev.get('detail','')}\n\n")
        else:
            clean_report += dl_news.get("raw_response", "No news data.") + "\n"

        clean_report += f"""
PENDING CATALYSTS
{'-'*40}
Upcoming Events: {research.get('pending_catalysts',{}).get('upcoming_events','N/A')}
Potential Upside: {research.get('pending_catalysts',{}).get('potential_upside','N/A')}
Potential Risks: {research.get('pending_catalysts',{}).get('potential_risks','N/A')}

VERDICT
{'-'*40}
Classification: {research.get('verdict',{}).get('classification','N/A')}
Confidence: {research.get('verdict',{}).get('confidence','N/A')}
Reasoning: {research.get('verdict',{}).get('reasoning','N/A')}
Price Context: {research.get('verdict',{}).get('price_context','N/A')}
"""
        report_text = clean_report

    st.download_button(
        label="📥 Download Research Report",
        data=report_text,
        file_name=f"microcap_report_{ticker}_{datetime.now().strftime('%Y%m%d')}.txt",
        mime="text/plain",
        use_container_width=True,
    )

# ── Footer ───────────────────────────────────────────────────────────────────
st.sidebar.divider()
st.sidebar.caption(
    "⚠️ This tool is for educational purposes only. Not financial advice. "
    "AI research may contain inaccuracies — always verify before investing."
    "Manuel Trachsler, April 2026"
)
