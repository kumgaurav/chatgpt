import yfinance as yf
import pandas as pd

tickers = ["AAPL", "GOOGL", "MSFT"]
start_date = "2024-06-01"
end_date = "2024-06-30"

# 📌 Download stock price data
data = yf.download(tickers, start=start_date, end=end_date, progress=False)

# ✅ Fixing the Pandas `stack` warning by specifying `future_stack=True`
data = data.stack(level=1, future_stack=True).reset_index()
data.rename(columns={"level_1": "Ticker"}, inplace=True)

# ✅ Save stock prices
price_filename = f"stock_prices_{start_date}.csv"
data.to_csv(price_filename, index=False)
print(f"📁 Stock prices saved as {price_filename}")

# 📌 Initialize lists for storing financial data
earnings_list = []
quarterly_revenue_list = []
growth_estimates_list = []
revenue_estimates_list = []

for ticker in tickers:
    stock = yf.Ticker(ticker)

    # ✅ Extract financial summary
    info = stock.info
    earnings_list.append({
        "Ticker": ticker,
        "Short Name": info.get("shortName", ""),
        "Sector": info.get("sector", ""),
        "Industry": info.get("industry", ""),
        "Market Cap": info.get("marketCap", ""),
        "Revenue": info.get("totalRevenue", ""),
        "Gross Profit": info.get("grossProfits", ""),
        "EBITDA": info.get("ebitda", ""),
        "Earnings Date": info.get("earningsDate", ""),
        "52-Week High": info.get("fiftyTwoWeekHigh", ""),
        "52-Week Low": info.get("fiftyTwoWeekLow", ""),
        "Dividend Yield": info.get("dividendYield", ""),
        "PE Ratio": info.get("trailingPE", ""),
        "Forward PE": info.get("forwardPE", ""),
    })

    # ✅ Extract quarterly revenue and gross profit
    try:
        quarterly_data = stock.quarterly_financials
        for quarter in quarterly_data.columns:
            quarterly_revenue_list.append({
                "Ticker": ticker,
                "Quarter": str(quarter),  # 🔹 Fix: Convert quarter to string to avoid `strftime` error
                "Revenue": quarterly_data.loc[
                    "Total Revenue", quarter] if "Total Revenue" in quarterly_data.index else None,
                "Gross Profit": quarterly_data.loc[
                    "Gross Profit", quarter] if "Gross Profit" in quarterly_data.index else None
            })
    except Exception as e:
        print(f"⚠️ Failed to get quarterly revenue for {ticker}: {e}")

    # ✅ Extract growth estimates (via recommendations)
    try:
        recommendations = stock.recommendations
        if recommendations is not None:
            latest_recommendation = recommendations.iloc[-1]
            growth_estimates_list.append({
                "Ticker": ticker,
                "Firm": latest_recommendation.get("Firm", ""),
                "Recommendation": latest_recommendation.get("To Grade", ""),
                "Action": latest_recommendation.get("Action", ""),
                "Date": str(latest_recommendation.name),  # 🔹 Fix: Convert date to string to avoid `strftime` error
            })
    except Exception as e:
        print(f"⚠️ Failed to get growth estimates for {ticker}: {e}")

    # ✅ Extract revenue estimates (if available)
    try:
        forecast = stock.get_income_stmt()
        if forecast is not None:
            revenue_estimates_list.append({
                "Ticker": ticker,
                "Total Revenue": forecast.loc["Total Revenue"].sum() if "Total Revenue" in forecast.index else None,
                "Gross Profit": forecast.loc["Gross Profit"].sum() if "Gross Profit" in forecast.index else None,
            })
    except Exception as e:
        print(f"⚠️ Failed to get revenue estimates for {ticker}: {e}")

# ✅ Convert extracted data to DataFrames
earnings_df = pd.DataFrame(earnings_list)
quarterly_revenue_df = pd.DataFrame(quarterly_revenue_list)
growth_estimates_df = pd.DataFrame(growth_estimates_list)
revenue_estimates_df = pd.DataFrame(revenue_estimates_list)

# ✅ Save data to CSV files
earnings_filename = f"stock_earnings_{start_date}.csv"
quarterly_revenue_filename = f"quarterly_revenue_{start_date}.csv"
growth_estimates_filename = f"growth_estimates_{start_date}.csv"
revenue_estimates_filename = f"revenue_estimates_{start_date}.csv"

earnings_df.to_csv(earnings_filename, index=False)
quarterly_revenue_df.to_csv(quarterly_revenue_filename, index=False)
growth_estimates_df.to_csv(growth_estimates_filename, index=False)
revenue_estimates_df.to_csv(revenue_estimates_filename, index=False)

print(f"📁 Earnings summary saved as {earnings_filename}")
print(f"📁 Quarterly revenue saved as {quarterly_revenue_filename}")
print(f"📁 Growth estimates saved as {growth_estimates_filename}")
print(f"📁 Revenue estimates saved as {revenue_estimates_filename}")
