import requests

API_KEY = "4EXCS4PU8RSZGUBR"
BASE_URL = "https://www.alphavantage.co/query"

def test_alphavantage_api():
    """Test alphavantage API with a simple stock quote request"""
    
    # Test 1: Get intraday data
    params = {
        "function": "INTRADAY",
        "symbol": "AAPL",
        "interval": "5min",
        "apikey": API_KEY
    }
    
    response = requests.get(BASE_URL, params=params)
    print(f"Status Code: {response.status_code}")
    data = response.json()
    
    if "Error Message" in data:
        print(f"Error: {data['Error Message']}")
    elif "Note" in data:
        print(f"Note: {data['Note']}")
    else:
        print(f"Success! Got data for {data.get('Meta Data', {}).get('2. Symbol', 'Unknown')}")
        print(f"Last Refreshed: {data.get('Meta Data', {}).get('3. Last Refreshed', 'N/A')}")

if __name__ == "__main__":
    test_alphavantage_api()
