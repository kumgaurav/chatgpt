import yfinance as yf
import pandas as pd
from datetime import datetime

# Example tickers
tickers = ["AAPL", "GOOGL", "MSFT","AI", "OPFI"]
quarterly_income_stmt_list = []

# Current date for reference
fetch_date = datetime(2025, 2, 21).strftime('%Y-%m-%d')

for ticker in tickers:
    stock = yf.Ticker(ticker)
    try:
        # Fetch quarterly income statement
        income_stmt = stock.quarterly_income_stmt
        if income_stmt is not None and not income_stmt.empty:
            # Iterate over all available quarters (columns)
            for quarter_date in income_stmt.columns:
                try:
                    revenue = income_stmt.loc["Total Revenue", quarter_date]  # Keep in millions
                    # Skip if revenue is None or NaN
                    if pd.isna(revenue):  # pd.isna() checks for both None and NaN
                        #print(f"⚠️ Skipping {ticker} for {quarter_date}: Revenue is empty")
                        continue
                    revenue_millions = revenue / 1e6
                    earnings = income_stmt.loc["Net Income", quarter_date]  # Keep in millions
                    earnings_millions = earnings / 1e6  # Convert to millions
                    #profits = income_stmt.loc["Gross Profit", quarter_date]  # Keep in millions
                    quarter_label = quarter_date.strftime('%Y-%m-%d')  # Quarter end date

                    quarterly_income_stmt_list.append({
                        "Ticker": ticker,
                        "Quarter": quarter_label,
                        "Revenue (M)": revenue_millions,
                        "Earnings (M)": earnings_millions,
                        #"Gross Profit (M)": earnings,
                    })
                except KeyError as e:
                    print(f"⚠️ Missing data for {ticker} on {quarter_date}: {e}")
            print(f"✅ Processed {ticker}")
        else:
            print(f"⚠️ {ticker} does not have financial data.")
    except Exception as e:
        print(f"⚠️ Failed to get financials for {ticker}: {e}")

# Convert to DataFrame and save
if quarterly_income_stmt_list:
    quarterly_income_df = pd.DataFrame(quarterly_income_stmt_list)
    quarterly_income_df["Revenue (M)"] = quarterly_income_df["Revenue (M)"].round(2)
    quarterly_income_df["Earnings (M)"] = quarterly_income_df["Earnings (M)"].round(2)
    # Optional: Sort by Ticker and Quarter for consistency
    quarterly_income_df = quarterly_income_df.sort_values(by=["Ticker", "Quarter"])
    quarterly_income_df.to_csv("all_tickers_quarterly_income.csv", index=False)
    print("✅ Saved to all_tickers_quarterly_income.csv")
else:
    print("⚠️ No quarterly_income data available.")