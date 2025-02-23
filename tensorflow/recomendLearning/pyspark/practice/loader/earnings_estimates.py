import yfinance as yf
import pandas as pd
from datetime import datetime

# Your list of tickers
tickers = ["AAPL", "MSFT", "GOOG"]  # Replace with your actual tickers

# Create a mapping for period codes to meaningful names
period_mapping = {
    "0q": "Current Quarter",
    "+1q": "Next Quarter",
    "0y": "Current Year",
    "+1y": "Next Year"
}
# Current date for reference (as provided: February 21, 2025)
fetch_date = datetime(2025, 2, 21).strftime('%Y-%m-%d')  # Format as YYYY-MM-DD

# Create an empty list to store earnings estimate data
earnings_data_list = []

for ticker in tickers:
    stock = yf.Ticker(ticker)

    # Get earnings estimates data
    earnings_estimates = stock.earnings_estimate

    # Check if we have data
    if earnings_estimates is not None and not earnings_estimates.empty:
        # Process each row in earnings estimates
        for index, row in earnings_estimates.iterrows():
            # Convert period code to meaningful name
            period_label = period_mapping.get(index, index)  # Use original if not in mapping

            # Create dictionary based on the actual structure you're seeing
            estimate_dict = {
                "Symbol": ticker,
                "Period": period_label,
                "Estimate Fetch Date": fetch_date,  # New field for when estimate was retrieved
                "Average Estimate": row.get('avg', None),
                "Low Estimate": row.get('low', None),
                "High Estimate": row.get('high', None),
                "Number of Analysts": row.get('numberOfAnalysts', None),
                "Year Ago EPS": row.get('yearAgoEps', None),
                "Growth": row.get('growth', None)
            }

            # Add to our list
            earnings_data_list.append(estimate_dict)
    else:
        print(f"No earnings estimate data available for {ticker}")

# Convert the list of dictionaries to a DataFrame
if earnings_data_list:
    earnings_df = pd.DataFrame(earnings_data_list)

    # Save to CSV
    earnings_df.to_csv("earnings_estimates.csv", index=False)
    print("Saved earnings estimates to earnings_estimates.csv")
else:
    print("No earnings estimate data to save")