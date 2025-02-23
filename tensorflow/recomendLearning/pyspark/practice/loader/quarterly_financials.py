import yfinance as yf
import pandas as pd

# Your list of tickers
tickers = ["AAPL", "MSFT", "GOOG"]  # Replace with your actual tickers

# Create an empty DataFrame to store the combined data
all_data = pd.DataFrame()

for ticker in tickers:
    stock = yf.Ticker(ticker)
    quarterly_data = stock.quarterly_financials

    if quarterly_data is not None and not quarterly_data.empty:
        # Transpose the dataframe so dates are rows
        quarterly_data_t = quarterly_data.T

        # Save the index (dates) as a column called 'quarter_end_date'
        quarterly_data_t = quarterly_data_t.reset_index()
        quarterly_data_t.rename(columns={'index': 'quarter_end_date'}, inplace=True)

        # Add the ticker symbol as a column
        quarterly_data_t['Symbol'] = ticker

        # Rearrange columns to put quarter_end_date first, then Symbol
        cols = quarterly_data_t.columns.tolist()
        # Remove quarter_end_date and Symbol from their current positions
        cols.remove('quarter_end_date')
        cols.remove('Symbol')
        # Add them at the beginning
        cols = ['quarter_end_date', 'Symbol'] + cols
        quarterly_data_t = quarterly_data_t[cols]

        # Append to the combined dataframe
        all_data = pd.concat([all_data, quarterly_data_t])

# Save the combined data to CSV
all_data.to_csv("all_tickers_quarterly_financials.csv", index=False)
print("Saved all quarterly financials to all_tickers_quarterly_financials.csv")