import yfinance as yf
import sys
print("Python version:", sys.version)

import pyspark
print("PySpark version:", pyspark.__version__)

ticker = yf.Ticker("AAPL")
print(ticker.earnings_dates)
