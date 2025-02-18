import yfinance as yf
import pandas as pd
from datetime import datetime

def get_financial_metrics(ticker_symbol):
    """
    Fetch revenue data and analyst guidance for a given ticker symbol.

    Parameters:
    ticker_symbol (str): Stock ticker symbol (e.g., 'AAPL', 'MSFT')

    Returns:
    dict: Dictionary containing revenue data and analyst recommendations
    """
    # Create ticker object
    ticker = yf.Ticker(ticker_symbol)

    try:
        # Get quarterly revenue
        quarterly_revenue = {}
        if hasattr(ticker, 'quarterly_financials'):
            if 'Total Revenue' in ticker.quarterly_financials.index:
                quarterly_revenue = ticker.quarterly_financials.loc['Total Revenue'].to_dict()

        # Get analyst recommendations
        recommendations = []
        if hasattr(ticker, 'recommendations') and ticker.recommendations is not None:
            recommendations = ticker.recommendations.reset_index().to_dict('records')

        # Get earnings history
        earnings_history = None
        try:
            earnings_history = ticker.earnings_history
            print("\nDebug - Earnings History:")
            print(earnings_history)
        except Exception as e:
            print(f"Error getting earnings history: {e}")

        # Get calendar data (includes earnings forecasts)
        calendar = None
        try:
            calendar = ticker.calendar
            print("\nDebug - Calendar Data:")
            print(calendar)
        except Exception as e:
            print(f"Error getting calendar data: {e}")

        # Get financial data
        financials = None
        try:
            financials = ticker.financials
            print("\nDebug - Financials:")
            print(financials.head())
        except Exception as e:
            print(f"Error getting financials: {e}")

        return {
            'quarterly_revenue': quarterly_revenue,
            'analyst_recommendations': recommendations,
            'earnings_history': earnings_history,
            'calendar': calendar,
            'financials': financials
        }

    except Exception as e:
        print(f"Error fetching data: {str(e)}")
        return None

def format_financial_data(financial_data):
    """
    Format the financial data into a readable format.
    """
    if not financial_data:
        return "No financial data available"

    output = []

    # Format quarterly revenue
    if financial_data['quarterly_revenue']:
        output.append("Quarterly Revenue (most recent first):")
        for date, revenue in financial_data['quarterly_revenue'].items():
            try:
                date_str = date.strftime('%Y-%m-%d') if isinstance(date, datetime) else str(date)
                if not pd.isna(revenue):  # Check for NaN values
                    output.append(f"  {date_str}: ${revenue:,.2f}")
            except Exception as e:
                output.append(f"  Error formatting date/revenue: {str(e)}")

    # Format analyst recommendations
    if financial_data['analyst_recommendations']:
        output.append("\nAnalyst Recommendations Summary:")

        for rec in financial_data['analyst_recommendations']:
            try:
                period = rec.get('period', 'N/A')
                strong_buy = rec.get('strongBuy', 0)
                buy = rec.get('buy', 0)
                hold = rec.get('hold', 0)
                sell = rec.get('sell', 0)
                strong_sell = rec.get('strongSell', 0)
                total = strong_buy + buy + hold + sell + strong_sell

                if total > 0:  # Only show if there are recommendations
                    output.append(f"\n  Period: {period}")
                    output.append(f"    Strong Buy: {strong_buy} ({strong_buy/total*100:.1f}%)")
                    output.append(f"    Buy: {buy} ({buy/total*100:.1f}%)")
                    output.append(f"    Hold: {hold} ({hold/total*100:.1f}%)")
                    output.append(f"    Sell: {sell} ({sell/total*100:.1f}%)")
                    output.append(f"    Strong Sell: {strong_sell} ({strong_sell/total*100:.1f}%)")
                    output.append(f"    Total Analysts: {total}")
            except Exception as e:
                output.append(f"  Error formatting recommendation: {str(e)}")

    # Format earnings history
    if financial_data.get('earnings_history') is not None:
        output.append("\nEarnings History:")
        try:
            history = financial_data['earnings_history']
            if isinstance(history, pd.DataFrame):
                for index, row in history.iterrows():
                    output.append(f"\n  Period: {index}")
                    for col in history.columns:
                        if pd.notna(row[col]):
                            output.append(f"    {col}: {row[col]}")
        except Exception as e:
            output.append(f"  Error formatting earnings history: {str(e)}")

    # Format calendar data
    if financial_data.get('calendar') is not None:
        output.append("\nUpcoming Events:")
        try:
            calendar = financial_data['calendar']
            if isinstance(calendar, pd.DataFrame):
                for index, row in calendar.iterrows():
                    output.append(f"\n  Event: {index}")
                    for col, val in row.items():
                        if pd.notna(val):
                            output.append(f"    {col}: {val}")
        except Exception as e:
            output.append(f"  Error formatting calendar data: {str(e)}")

    # Format financials
    if financial_data.get('financials') is not None:
        output.append("\nFinancial Data:")
        try:
            financials = financial_data['financials']
            if isinstance(financials, pd.DataFrame):
                for col in financials.columns:
                    output.append(f"\n  Period: {col}")
                    for index, value in financials[col].items():
                        if pd.notna(value):
                            output.append(f"    {index}: ${value:,.2f}" if isinstance(value, (int, float)) else f"    {index}: {value}")
        except Exception as e:
            output.append(f"  Error formatting financials: {str(e)}")

    return "\n".join(output)

# Example usage
if __name__ == "__main__":
    # Example for Apple Inc.
    ticker_symbol = "AAPL"

    try:
        financial_data = get_financial_metrics(ticker_symbol)
        if financial_data:
            formatted_output = format_financial_data(financial_data)
            print(f"\nFinancial Analysis for {ticker_symbol}:")
            print(formatted_output)
        else:
            print(f"Unable to fetch data for {ticker_symbol}")
    except Exception as e:
        print(f"Error in main execution: {str(e)}")
