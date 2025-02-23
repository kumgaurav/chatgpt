import yfinance as yf
import pandas as pd
# Create a mapping for period codes to meaningful names
period_mapping = {
    "0q": "Current Quarter",
    "+1q": "Next Quarter",
    "0y": "Current Year",
    "+1y": "Next Year",
    "LTG": "Long-Term Growth"
}

tickers = ["AAPL", "GOOGL", "MSFT","AI", "OPFI"]
start_date = "2024-06-01"
end_date = "2024-06-30"

# 📌 Download stock price data
data = yf.download(tickers, start=start_date, end=end_date, progress=False)

# ✅ Convert MultiIndex columns to normal DataFrame with tickers as a column
data = data.stack(level=1, future_stack=True).reset_index()
data.rename(columns={"level_1": "Ticker"}, inplace=True)

# ✅ Save stock prices
price_filename = f"stock_prices_{start_date}.csv"
data.to_csv(price_filename, index=False)
print(f"📁 Stock prices saved as {price_filename}")

# 📌 Initialize lists for storing financial data
earnings_list = []
earnings_estimate_list = []
earnings_history_list = []
quarterly_revenue_list = []
growth_estimates_list = []
revenue_estimates_list = []
quarterly_income_stmt_list = []

for ticker in tickers:
    stock = yf.Ticker(ticker)
    info = stock.info  # Get stock information

    # ✅ Extract financial summary (Revenue, Market Cap, etc.)
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
        print("\n📌 Quarterly Schema:")
        if quarterly_data is not None and not quarterly_data.empty:
            # For each quarter (column) in the quarterly data
            for quarter in quarterly_data.columns:
                revenue = quarterly_data.loc["Total Revenue", quarter] if "Total Revenue" in quarterly_data.index else None # Keep in millions
                if pd.isna(revenue):  # pd.isna() checks for both None and NaN
                    # print(f"⚠️ Skipping {ticker} for {quarter_date}: Revenue is empty")
                    continue
                gross_profit = quarterly_data.loc["Gross Profit", quarter] if "Gross Profit" in quarterly_data.index else None
                # Create a dictionary with only the fields you want
                quarter_dict = {
                    "Symbol": ticker,
                    "quarter_end_date": quarter.strftime("%Y-%m-%d"),
                    "Revenue": revenue,
                    "Gross Profit": gross_profit,
                }
                # Add this quarter's data to our list
                quarterly_revenue_list.append(quarter_dict)
    except Exception as e:
        print(f"⚠️ Failed to get quarterly revenue for {ticker}: {e}")

    # ✅ Extract earnings estimates
    try:
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
                    "Estimate Fetch Date": start_date,  # New field for when estimate was retrieved
                    "Average Estimate": row.get('avg', None),
                    "Low Estimate": row.get('low', None),
                    "High Estimate": row.get('high', None),
                    "Number of Analysts": row.get('numberOfAnalysts', None),
                    "Year Ago EPS": row.get('yearAgoEps', None),
                    "Growth": row.get('growth', None)
                }
                # Add to our list
                earnings_estimate_list.append(estimate_dict)
        else:
            print(f"No earnings estimate data available for {ticker}")
    except Exception as e:
        print(f"⚠️ Failed to get earnings estimates for {ticker}: {e}")

    # ✅ Extract earnings history
    try:
        earnings_history = stock.earnings_history  # Ensure this is a DataFrame
        for quarter, row in earnings_history.iterrows():
            earnings_history_list.append({
                "Ticker": ticker,
                "Earnings Date": quarter,  # Using index as the date
                "Reported EPS": row["epsActual"],
                "Estimate EPS": row["epsEstimate"],
                "Surprise Percentage": row["surprisePercent"],
            })
    except AttributeError:
        print(f"⚠️ {ticker} does not have earnings history.")
    except Exception as e:
        print(f"⚠️ Failed to get earnings history for {ticker}: {e}")

    # ✅ Extract growth estimates
    try:
        growth_estimates = stock.growth_estimates
        if growth_estimates is not None and not growth_estimates.empty:
            for index, row in growth_estimates.iterrows():
                stock_growth = row.get("stockTrend", None)
                index_growth = row.get("indexTrend", None)
                # Map the period code to a readable name, default to original if not in mapping
                period_label = period_mapping.get(index, index)
                if stock_growth is not None or index_growth is not None:
                    growth_estimates_list.append({
                        "Estimate Fetch Date": start_date,  # Added fetch date
                        "Ticker": ticker,
                        "Period": period_label,
                        "Stock Growth": stock_growth,
                        "Index Growth": index_growth,
                    })
            print(f"✅ Processed {ticker}")
        else:
            print(f"⚠️ {ticker} does not have growth estimates.")
    except Exception as e:
        print(f"⚠️ Failed to get growth estimates for {ticker}: {e}")

    # ✅ Extract revenue estimates
    try:
        revenue_estimates = stock.revenue_estimate
        # Check if we have data
        if revenue_estimates is not None and not revenue_estimates.empty:
            # Process each row in revenue forecasts
            for index, row in revenue_estimates.iterrows():
                period_label = period_mapping.get(index, index)  # Use original if not in mapping

                # Create dictionary with revenue forecast data including fetch date
                revenue_estimate_dict = {
                    "Symbol": ticker,
                    "Period": period_label,
                    "Estimate Fetch Date": start_date,  # New field for when estimate was retrieved
                    "Average Forecast": row.get('avg', None),
                    "Low Forecast": row.get('low', None),
                    "High Forecast": row.get('high', None),
                    "Number of Analysts": row.get('numberOfAnalysts', None),
                    "Year Ago Revenue": row.get('yearAgoRevenue', None),
                    "Growth": row.get('growth', None)
                }

                # Add to our list
                revenue_estimates_list.append(revenue_estimate_dict)
        else:
            print(f"No revenue forecast data available for {ticker}")
    except Exception as e:
        print(f"⚠️ Failed to get revenue estimates for {ticker}: {e}")

    # ✅ Extract quarterly income statement
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
                    # profits = income_stmt.loc["Gross Profit", quarter_date]  # Keep in millions
                    quarter_label = quarter_date.strftime('%Y-%m-%d')  # Quarter end date

                    quarterly_income_stmt_list.append({
                        "Ticker": ticker,
                        "Quarter": quarter_label,
                        "Revenue (M)": revenue_millions,
                        "Earnings (M)": earnings_millions,
                        # "Gross Profit (M)": earnings,
                    })
                except KeyError as e:
                    print(f"⚠️ Missing data for {ticker} on {quarter_date}: {e}")
            print(f"✅ Processed {ticker}")
        else:
            print(f"⚠️ {ticker} does not have financial data.")
    except Exception as e:
        print(f"⚠️ Failed to get financials for {ticker}: {e}")

# ✅ Convert extracted data to DataFrames
earnings_df = pd.DataFrame(earnings_list)
earnings_estimate_df = pd.DataFrame(earnings_estimate_list)
earnings_history_df = pd.DataFrame(earnings_history_list)
quarterly_revenue_df = pd.DataFrame(quarterly_revenue_list)
growth_estimates_df = pd.DataFrame(growth_estimates_list)
revenue_estimates_df = pd.DataFrame(revenue_estimates_list)
quarterly_income_df = pd.DataFrame(quarterly_income_stmt_list)
quarterly_income_df["Revenue (M)"] = quarterly_income_df["Revenue (M)"].round(2)
quarterly_income_df["Earnings (M)"] = quarterly_income_df["Earnings (M)"].round(2)

# ✅ Save data to CSV files
earnings_filename = f"stock_earnings_{start_date}.csv"
earnings_estimate_filename = f"earnings_estimates_{start_date}.csv"
earnings_history_filename = f"earnings_history_{start_date}.csv"
quarterly_revenue_filename = f"quarterly_revenue_{start_date}.csv"
growth_estimates_filename = f"growth_estimates_{start_date}.csv"
revenue_estimates_filename = f"revenue_estimates_{start_date}.csv"
quarterly_income_filename = f"quarterly_income_{start_date}.csv"

earnings_df.to_csv(earnings_filename, index=False)
earnings_estimate_df.to_csv(earnings_estimate_filename, index=False)
earnings_history_df.to_csv(earnings_history_filename, index=False)
quarterly_revenue_df.to_csv(quarterly_revenue_filename, index=False)
growth_estimates_df.to_csv(growth_estimates_filename, index=False)
revenue_estimates_df.to_csv(revenue_estimates_filename, index=False)
quarterly_income_df.to_csv(quarterly_income_filename, index=False)

print(f"📁 Earnings summary saved as {earnings_filename}")
print(f"📁 Earnings estimates saved as {earnings_estimate_filename}")
print(f"📁 Earnings history saved as {earnings_history_filename}")
print(f"📁 Quarterly revenue saved as {quarterly_revenue_filename}")
print(f"📁 Growth estimates saved as {growth_estimates_filename}")
print(f"📁 Revenue estimates saved as {revenue_estimates_filename}")
print(f"📁 Quarterly income saved as {quarterly_income_filename}")