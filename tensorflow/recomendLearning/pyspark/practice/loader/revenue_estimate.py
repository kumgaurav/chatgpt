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

# Create an empty list to store revenue forecast data
revenue_estimate_list = []

for ticker in tickers:
    stock = yf.Ticker(ticker)

    # Get revenue forecast data
    revenue_estimate = stock.revenue_estimate

    # Check if we have data
    if revenue_estimate is not None and not revenue_estimate.empty:
        # Process each row in revenue forecasts
        for index, row in revenue_estimate.iterrows():
            period_label = period_mapping.get(index, index)  # Use original if not in mapping

            # Create dictionary with revenue forecast data including fetch date
            revenue_estimate_dict = {
                "Symbol": ticker,
                "Period": period_label,
                "Estimate Fetch Date": fetch_date,  # New field for when estimate was retrieved
                "Average Forecast": row.get('avg', None),
                "Low Forecast": row.get('low', None),
                "High Forecast": row.get('high', None),
                "Number of Analysts": row.get('numberOfAnalysts', None),
                "Year Ago Revenue": row.get('yearAgoRevenue', None),
                "Growth": row.get('growth', None)
            }

            # Add to our list
            revenue_estimate_list.append(revenue_estimate_dict)
    else:
        print(f"No revenue forecast data available for {ticker}")

# Convert the list of dictionaries to a DataFrame
if revenue_estimate_list:
    revenue_df = pd.DataFrame(revenue_estimate_list)

    # Save to CSV
    revenue_df.to_csv("revenue_estimate.csv", index=False)
    print("Saved revenue forecasts to revenue_estimate.csv")
else:
    print("No revenue forecast data to save")