import time, random
import numpy as np
import pandas as pd
import yfinance as yf
from urllib.error import HTTPError
import requests
from requests.exceptions import HTTPError
from curl_cffi import requests


def get_info_with_retry(stock, attempts=3):
    for i in range(attempts):
        try:
            return stock.get_info() if hasattr(stock, "get_info") else stock.info
        except Exception as e:
            if "429" in str(e):
                time.sleep((2 ** i) + random.uniform(0.2, 0.8))
                continue
            raise
    return {}

def _normalize_earnings_date(value, default_date_str: str) -> str:
    """Return ISO date string. Fallback to default_date_str if value is missing/invalid.

    yfinance may return a scalar Timestamp, a list/tuple of dates, or None.
    """
    try:
        candidate = None
        if isinstance(value, (list, tuple)) and len(value) > 0:
            candidate = value[0]
        else:
            candidate = value

        ts = pd.to_datetime(candidate, errors='coerce')
        if pd.isna(ts):
            return default_date_str
        return ts.date().isoformat()
    except Exception:
        return default_date_str

fetch_date = "2025-06-01"
ticker = "AAPL"
session = requests.Session(impersonate="chrome")
stock = yf.Ticker(ticker, session=session)
info = get_info_with_retry(stock)
print(yf.__version__)
upcoming_raw = None
try:
    cal = stock.calendar
    if isinstance(cal, pd.DataFrame) and 'Earnings Date' in cal.index:
        # yfinance often returns a single-column DF with index labels
        val = cal.loc['Earnings Date']
        if isinstance(val, pd.Series) and len(val) > 0:
            upcoming_raw = val.iloc[0]
        else:
            upcoming_raw = val
    elif isinstance(cal, dict):
        upcoming_raw = cal.get('Earnings Date', None)
except Exception as e:
    print(f"[yfinance] calendar fetch error for {ticker}: {e}")

if upcoming_raw is None:
    upcoming_raw = info.get('earningsDate', None)

upcoming_raw = info.get('earningsDate', None)
normalized_edate = _normalize_earnings_date(upcoming_raw, fetch_date)
print(f"[yfinance] {ticker} upcoming_raw={repr(upcoming_raw)} -> normalized={normalized_edate}")
