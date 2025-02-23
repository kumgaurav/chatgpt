import yfinance as yf
import sys
import pyspark
import pandas as pd

# Expected - 3.10.9
print("Python version:", sys.version)
# Spark - 3.2.1
print("PySpark version:", pyspark.__version__)
# Finance - 0.2.54
print("yahoo finance version: ", yf.__version__)
# pandas - 2.2.2
print(pd.__version__)
