import time, random
import numpy as np
import pandas as pd
import yfinance as yf
from urllib.error import HTTPError
import requests
from requests.exceptions import HTTPError
from curl_cffi import requests
import os
import argparse
from datetime import date

# Set environment variables to handle SSL issues
os.environ['CURL_CA_BUNDLE'] = ''
os.environ['REQUESTS_CA_BUNDLE'] = ''

# Configure curl_cffi to skip SSL verification
os.environ['CURL_CFG_SSL_VERIFY'] = '0'
os.environ['CURL_CFG_SSL_VERIFYHOST'] = '0'

# Note: yfinance handles its own session management with curl_cffi
# We don't need to create a custom session
# Create a mapping for period codes to meaningful names
period_mapping = {
    "0q": "Current Quarter",
    "+1q": "Next Quarter",
    "0y": "Current Year",
    "+1y": "Next Year",
    "LTG": "Long-Term Growth"
}

def get_info_with_retry(stock, attempts=3):
    for i in range(attempts):
        try:
            return stock.get_info() if hasattr(stock, "get_info") else stock.info
        except Exception as e:
            if "429" in str(e):
                time.sleep((2 ** i) + random.uniform(0.2, 0.8))
                continue
            raise
    return {}

def _normalize_earnings_date(value, default_date_str: str) -> str:
    """Return ISO date string. Fallback to default_date_str if value is missing/invalid.

    yfinance may return a scalar Timestamp, a list/tuple of dates, or None.
    """
    try:
        candidate = None
        if isinstance(value, (list, tuple)) and len(value) > 0:
            candidate = value[0]
        else:
            candidate = value

        ts = pd.to_datetime(candidate, errors='coerce')
        if pd.isna(ts):
            return default_date_str
        return ts.date().isoformat()
    except Exception:
        return default_date_str

def get_yfinance_dataframes(tickers, fetch_date):
    """
    Fetch multiple yfinance datasets for a list of tickers and return DataFrames.

    Args:
        tickers (List[str]): List of ticker symbols.
        fetch_date (str): Date string (YYYY-MM-DD) to stamp pulled estimates.

    Returns:
        dict: Mapping of dataset name to pandas DataFrame.
    """
    # 📌 Initialize lists for storing financial data
    earnings_list = []
    earnings_estimate_list = []
    earnings_history_list = []
    quarterly_revenue_list = []
    growth_estimates_list = []
    revenue_estimates_list = []
    quarterly_income_stmt_list = []
    stock_details_list = []

    tickers = tickers or []
    for ticker in tickers:
        time.sleep(1.2 + random.uniform(0, 0.6))  # gentle throttle per ticker
        session = requests.Session(impersonate="chrome")
        stock = yf.Ticker(ticker, session=session)
        info = get_info_with_retry(stock)

        # Resolve upcoming earnings date from calendar (preferred) or info fallback
        upcoming_raw = None
        try:
            cal = stock.calendar
            if isinstance(cal, pd.DataFrame) and 'Earnings Date' in cal.index:
                # yfinance often returns a single-column DF with index labels
                val = cal.loc['Earnings Date']
                if isinstance(val, pd.Series) and len(val) > 0:
                    upcoming_raw = val.iloc[0]
                else:
                    upcoming_raw = val
            elif isinstance(cal, dict):
                upcoming_raw = cal.get('Earnings Date', None)
        except Exception as e:
            print(f"[yfinance] calendar fetch error for {ticker}: {e}")

        if upcoming_raw is None:
            upcoming_raw = info.get('earningsDate', None)

        normalized_edate = _normalize_earnings_date(upcoming_raw, fetch_date)
        print(f"[yfinance] {ticker} upcoming_raw={repr(upcoming_raw)} -> normalized={normalized_edate}")

        earnings_list.append({
            "ticker": ticker,
            "short_name": info.get("shortName", ""),
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
            "market_cap": info.get("marketCap", ""),
            "revenue": info.get("totalRevenue", ""),
            "gross_profit": info.get("grossProfits", ""),
            "ebitda": info.get("ebitda", ""),
            "earnings_date": normalized_edate,
            "fifty_two_week_high": info.get("fiftyTwoWeekHigh", ""),
            "fifty_two_week_low": info.get("fiftyTwoWeekLow", ""),
            "dividend_yield": info.get("dividendYield", ""),
            "pe_ratio": info.get("trailingPE", ""),
            "forward_pe": info.get("forwardPE", ""),
        })

        # ✅ Extract stock details (company fundamentals and metadata)
        stock_details_list.append({
            "Symbol": ticker,
            "company_name": info.get("longName", ticker),
            "sector": info.get("sector", "Unknown"),
            "industry": info.get("industry", "Unknown"),
            "website": info.get("website", ""),
            "business_summary": info.get("longBusinessSummary", ""),
            "full_time_employees": info.get("fullTimeEmployees", None),
            "city": info.get("city", ""),
            "state": info.get("state", ""),
            "country": info.get("country", ""),
            "phone": info.get("phone", ""),
            "market_cap": info.get("marketCap", None),
            "enterprise_value": info.get("enterpriseValue", None),
            "trailing_pe": info.get("trailingPE", None),
            "forward_pe": info.get("forwardPE", None),
            "price_to_book": info.get("priceToBook", None),
            "revenue_ttm": info.get("totalRevenue", None),
            "gross_margins": info.get("grossMargins", None),
            "profit_margins": info.get("profitMargins", None),
        })

        # ✅ Extract quarterly revenue and gross profit
        try:
            quarterly_data = stock.quarterly_financials
            if quarterly_data is not None and not quarterly_data.empty:
                for quarter in quarterly_data.columns:
                    revenue = (
                        quarterly_data.loc["Total Revenue", quarter]
                        if "Total Revenue" in quarterly_data.index
                        else None
                    )
                    if pd.isna(revenue):
                        continue
                    gross_profit = (
                        quarterly_data.loc["Gross Profit", quarter]
                        if "Gross Profit" in quarterly_data.index
                        else None
                    )
                    quarter_dict = {
                        "ticker": ticker,
                        "report_date": quarter.strftime("%Y-%m-%d"),
                        "revenue": revenue,
                        "gross_profit": gross_profit,
                    }
                    quarterly_revenue_list.append(quarter_dict)
        except Exception as e:
            print(f"⚠️ Failed to get quarterly revenue for {ticker}: {e}")

        # ✅ Extract earnings estimates
        try:
            earnings_estimates = stock.earnings_estimate
            if earnings_estimates is not None and not earnings_estimates.empty:
                for index, row in earnings_estimates.iterrows():
                    period_label = period_mapping.get(index, index)
                    estimate_dict = {
                        "ticker": ticker,
                        "period": period_label,
                        "earnings_date": fetch_date,
                        "average_estimate": row.get('avg', None),
                        "low_estimate": row.get('low', None),
                        "high_estimate": row.get('high', None),
                        "number_of_analysts": row.get('numberOfAnalysts', None),
                        "year_ago_eps": row.get('yearAgoEps', None),
                        "growth": row.get('growth', None)
                    }
                    earnings_estimate_list.append(estimate_dict)
            else:
                print(f"No earnings estimate data available for {ticker}")
        except Exception as e:
            print(f"⚠️ Failed to get earnings estimates for {ticker}: {e}")

        # ✅ Extract earnings history
        try:
            earnings_history = stock.earnings_history
            for quarter, row in earnings_history.iterrows():
                earnings_history_list.append({
                    "ticker": ticker,
                    "earnings_date": quarter,
                    "reported_eps": row.get("epsActual", None),
                    "estimate_eps": row.get("epsEstimate", None),
                    "surprise_percentage": row.get("surprisePercent", None),
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
                    period_label = period_mapping.get(index, index)
                    if stock_growth is not None or index_growth is not None:
                        growth_estimates_list.append({
                            "estimate_fetch_date": fetch_date,
                            "ticker": ticker,
                            "period": period_label,
                            "stock_growth": stock_growth,
                            "index_growth": index_growth,
                        })
            else:
                print(f"⚠️ {ticker} does not have growth estimates.")
        except Exception as e:
            print(f"⚠️ Failed to get growth estimates for {ticker}: {e}")

        # ✅ Extract revenue estimates
        try:
            revenue_estimates = stock.revenue_estimate
            if revenue_estimates is not None and not revenue_estimates.empty:
                for index, row in revenue_estimates.iterrows():
                    period_label = period_mapping.get(index, index)
                    revenue_estimate_dict = {
                        "ticker": ticker,
                        "period": period_label,
                        "next_earnings_date": fetch_date,
                        "average_forecast": row.get('avg', None),
                        "low_forecast": row.get('low', None),
                        "high_forecast": row.get('high', None),
                        "number_of_analysts": row.get('numberOfAnalysts', None),
                        "year_ago_revenue": row.get('yearAgoRevenue', None),
                        "growth": row.get('growth', None)
                    }
                    revenue_estimates_list.append(revenue_estimate_dict)
            else:
                print(f"No revenue forecast data available for {ticker}")
        except Exception as e:
            print(f"⚠️ Failed to get revenue estimates for {ticker}: {e}")

        # ✅ Extract quarterly income statement
        try:
            income_stmt = stock.quarterly_income_stmt
            if income_stmt is not None and not income_stmt.empty:
                for quarter_date in income_stmt.columns:
                    try:
                        revenue = income_stmt.loc["Total Revenue", quarter_date]
                        if pd.isna(revenue):
                            continue
                        revenue_millions = revenue / 1e6
                        earnings = income_stmt.loc["Net Income", quarter_date]
                        earnings_millions = earnings / 1e6
                        quarter_label = quarter_date.strftime('%Y-%m-%d')

                        quarterly_income_stmt_list.append({
                            "ticker": ticker,
                            "report_date": quarter_label,
                            "revenue_m": revenue_millions,
                            "earnings_m": earnings_millions,
                        })
                    except KeyError as e:
                        print(f"⚠️ Missing data for {ticker} on {quarter_date}: {e}")
            else:
                print(f"⚠️ {ticker} does not have financial data.")
        except Exception as e:
            print(f"⚠️ Failed to get financials for {ticker}: {e}")

    # ✅ Convert extracted data to DataFrames
    print(f"[yfinance] collected sizes: earnings={len(earnings_list)}, est={len(earnings_estimate_list)}, hist={len(earnings_history_list)}, qrev={len(quarterly_revenue_list)}, growth={len(growth_estimates_list)}, rev_est={len(revenue_estimates_list)}, qinc={len(quarterly_income_stmt_list)}")

    earnings_df = pd.DataFrame(earnings_list)
    earnings_estimate_df = pd.DataFrame(earnings_estimate_list)
    earnings_history_df = pd.DataFrame(earnings_history_list)
    quarterly_revenue_df = pd.DataFrame(quarterly_revenue_list)
    growth_estimates_df = pd.DataFrame(growth_estimates_list)
    revenue_estimates_df = pd.DataFrame(revenue_estimates_list)
    quarterly_income_df = pd.DataFrame(quarterly_income_stmt_list)
    if not quarterly_income_df.empty:
        quarterly_income_df["revenue_m"] = quarterly_income_df["revenue_m"].round(2)
        quarterly_income_df["earnings_m"] = quarterly_income_df["earnings_m"].round(2)

    return {
        'earnings': earnings_df,
        'earnings_estimate': earnings_estimate_df,
        'earnings_history': earnings_history_df,
        'quarterly_revenue': quarterly_revenue_df,
        'growth_estimates': growth_estimates_df,
        'revenue_estimates': revenue_estimates_df,
        'quarterly_income': quarterly_income_df,
        'stock_details': pd.DataFrame(stock_details_list),
    }


def main():
    parser = argparse.ArgumentParser(description="Quick test harness for yfinance utils")
    parser.add_argument(
        "-t",
        "--tickers",
        type=str,
        default="AAPL,MSFT",
        help="Comma-separated list of ticker symbols (default: AAPL,MSFT)",
    )
    parser.add_argument(
        "-d",
        "--fetch-date",
        type=str,
        default=date.today().isoformat(),
        help="Fetch date used to stamp estimates (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--save-csv",
        action="store_true",
        help="If provided, saves resulting DataFrames to CSV files.",
    )
    parser.add_argument(
        "--outdir",
        type=str,
        default="",
        help="Directory to write CSVs to when --save-csv is set. Defaults to current directory.",
    )

    args = parser.parse_args()
    ticker_list = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if not ticker_list:
        print("No valid tickers provided. Exiting.")
        return

    print(f"Fetching yfinance datasets for tickers={ticker_list} fetch_date={args.fetch_date}")
    results = get_yfinance_dataframes(ticker_list, args.fetch_date)

    # Print a concise summary for quick sanity check
    for name, df in results.items():
        try:
            shape_repr = getattr(df, "shape", None)
            num_rows = shape_repr[0] if shape_repr else "?"
            num_cols = shape_repr[1] if shape_repr else "?"
            preview_cols = list(df.columns)[:6] if hasattr(df, "columns") else []
            print(f"- {name}: rows={num_rows} cols={num_cols} sample_cols={preview_cols}")
        except Exception as e:
            print(f"- {name}: failed to summarize DataFrame: {e}")

    # Show a small head for the most commonly used frames
    for key in ["earnings", "stock_details"]:
        df = results.get(key)
        if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
            print(f"\n{key} (head):")
            print(df.head(5).to_string(index=False))

    # Optionally save outputs
    if args.save_csv:
        output_directory = args.outdir or os.getcwd()
        os.makedirs(output_directory, exist_ok=True)
        for name, df in results.items():
            if isinstance(df, pd.DataFrame) and not df.empty:
                output_path = os.path.join(output_directory, f"{name}.csv")
                try:
                    df.to_csv(output_path, index=False)
                    print(f"Saved {name} -> {output_path}")
                except Exception as e:
                    print(f"Failed to save {name} to {output_path}: {e}")


if __name__ == "__main__":
    main()