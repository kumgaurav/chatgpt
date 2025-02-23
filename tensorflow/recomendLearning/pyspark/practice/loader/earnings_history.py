import yfinance as yf
import pandas as pd

tickers = ["AI", "MSFT", "OPFI"]  # Replace with your actual tickers
earnings_history_list = []

for ticker in tickers:
    stock = yf.Ticker(ticker)
    try:
        earnings_history = stock.earnings_history  # Ensure this is a DataFrame

        if earnings_history is not None and not earnings_history.empty:
            for quarter, row in earnings_history.iterrows():
                earnings_history_list.append({
                    "Ticker": ticker,
                    "Earnings Date": quarter,  # Using index as the date
                    "Reported EPS": row.get("epsActual"),  # .get() to avoid KeyErrors
                    "Estimate EPS": row.get("epsEstimate"),
                    "Surprise Percentage": row.get("surprisePercent"),
                })
        else:
            print(f"⚠️ {ticker} does not have earnings history.")

    except AttributeError:
        print(f"⚠️ {ticker} does not have earnings history.")
    except Exception as e:
        print(f"⚠️ Failed to get earnings history for {ticker}: {e}")

# Convert to DataFrame and save if data exists
if earnings_history_list:
    earnings_history_df = pd.DataFrame(earnings_history_list)
    earnings_history_df.to_csv("all_tickers_earnings_history.csv", index=False)
    print("✅ Saved all quarterly financials to all_tickers_earnings_history.csv")
else:
    print("⚠️ No earnings history available for any tickers.")
