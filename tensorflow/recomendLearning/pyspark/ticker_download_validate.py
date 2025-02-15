import yfinance as yf
import sys
import pandas as pd

def save_data(df, filename):
    df.to_csv('./data/' + filename + '.csv')

start_date = "2025-02-10"
end_date="2025-02-14"
# Define a list of ticker symbols to download
tickerStrings = 'AAPL'
dataname = tickerStrings + '_' + str(end_date)
# Download 2 days of data for each ticker, grouping by 'Ticker' to structure the DataFrame with multi-level columns
df = yf.download(tickerStrings, group_by='Ticker', start=start_date, end=end_date, progress=False, rounding=True)
print(df.columns)
# Transform the DataFrame: stack the ticker symbols to create a multi-index (Date, Ticker), then reset the 'Ticker' level to turn it into a column
df = df.stack(level=0).rename_axis(['Date', 'Ticker']).reset_index(level=1)
#df.to_csv('./data/ticker.csv')
df["symbol"] = tickerStrings
# Add 'price_change' column (Close Price - Previous Close)
df["Adj Close"] = df["Close"]
df["price_change"] = df["Close"] - df["Open"]  # NaN for the first row
df = df.drop(columns=["Ticker"])
# Display result
print(df.head())
save_data(df,dataname)

