import yfinance as yf
import sys
print("Python version:", sys.version)

import pyspark
print("PySpark version:", pyspark.__version__)
print("yahoo finance version: ",yf.__version__)
ticker = yf.Ticker("TTD")
#print(ticker.earnings_dates)
# Fetch revenue from the income statement
# income_statement = ticker.financials
#rstimate = ticker.revenue_estimate

# Check available keys
#print("Available rows in income statement:", income_statement.index)
#print("Available rows in revenue_estimate:", rstimate.index)




# Fetch quarterly financials (Income Statement)
quarterly_financials = ticker.quarterly_income_stmt

# Check available keys
print("Available rows in income statement:", quarterly_financials.index)

# Extract Total Revenue (sometimes labeled as 'Revenue')
revenue = quarterly_financials.loc["Total Revenue"] if "Total Revenue" in quarterly_financials.index else None

# Display results
print("\nQuarterly Revenue Data:\n", revenue)



# Extract revenue (ensure correct label is used)
#estimate_0y = rstimate.loc["0y"] if "0q" in rstimate.index else None
#estimate_1y = rstimate.loc["+1y"] if "0q" in rstimate.index else None

# Display results
#print("\nestimate_0y Data:\n", estimate_0y)
#print("\nestimate_1y Data:\n", estimate_1y)


