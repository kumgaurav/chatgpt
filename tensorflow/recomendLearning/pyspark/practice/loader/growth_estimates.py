import yfinance as yf
import pandas as pd
from datetime import datetime

# Example tickers (replace with yours)
tickers = ["AI", "MSFT", "OPFI"]
growth_estimates_list = []

# Current date for reference (as provided: February 21, 2025)
fetch_date = datetime(2025, 2, 21).strftime('%Y-%m-%d')  # Format as YYYY-MM-DD

# Create a mapping for period codes to meaningful names
period_mapping = {
    "0q": "Current Quarter",
    "+1q": "Next Quarter",
    "0y": "Current Year",
    "+1y": "Next Year",
    "LTG": "Long-Term Growth"
}

for ticker in tickers:
    stock = yf.Ticker(ticker)
    try:
        growth_estimates = stock.growth_estimates
        print(f"Growth estimates for {ticker}:")
        print(growth_estimates)

        if growth_estimates is not None and not growth_estimates.empty:
            for index, row in growth_estimates.iterrows():
                stock_growth = row.get("stockTrend", None)
                index_growth = row.get("indexTrend", None)
                # Map the period code to a readable name, default to original if not in mapping
                period_label = period_mapping.get(index, index)
                if stock_growth is not None or index_growth is not None:
                    growth_estimates_list.append({
                        "Estimate Fetch Date": fetch_date,  # Added fetch date
                        "Ticker": ticker,
                        "Period": period_label,
                        "Stock Growth": stock_growth,
                        "Index Growth": index_growth,
                    })
            print(f"✅ Processed {ticker}")
        else:
            print(f"⚠️ {ticker} does not have growth estimates.")

    except AttributeError:
        print(f"⚠️ {ticker} does not have growth estimates attribute.")
    except Exception as e:
        print(f"⚠️ Failed to get growth estimates for {ticker}: {e}")

# Convert to DataFrame and save if data exists
if growth_estimates_list:
    growth_estimates_df = pd.DataFrame(growth_estimates_list)
    # Convert growth estimates to percentage format, handling NaN
    for col in ["Stock Growth", "Index Growth"]:
        growth_estimates_df[col] = growth_estimates_df[col].apply(
            lambda x: f"{x*100:.2f}%" if pd.notna(x) else "N/A"
        )
    growth_estimates_df.to_csv("all_tickers_growth_estimates.csv", index=False)
    print("✅ Saved all growth estimates to all_tickers_growth_estimates.csv")
else:
    print("⚠️ No growth estimates available for any tickers.")