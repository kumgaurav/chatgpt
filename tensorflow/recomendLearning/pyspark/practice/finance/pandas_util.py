from pyspark.sql import DataFrame as SparkDataFrame
import pandas as pd


def convert_to_yfinance_format(spark_df: SparkDataFrame) -> pd.DataFrame:
    """
    Convert a Spark DataFrame with stock data to a Pandas DataFrame in yfinance format.
    Handles type conversion from strings to numeric values.

    Parameters:
    -----------
    spark_df : pyspark.sql.DataFrame
        Spark DataFrame containing stock data with columns: Date, Ticker, Open, High, Low, Close, Volume

    Returns:
    --------
    pd.DataFrame
        Pandas DataFrame with multi-level columns similar to yfinance output
    """
    # Convert Spark DataFrame to Pandas
    pdf = spark_df.toPandas()

    # Convert numeric columns to float
    numeric_columns = ['Open', 'High', 'Low', 'Close']
    for col in numeric_columns:
        pdf[col] = pd.to_numeric(pdf[col], errors='coerce')

    # Convert Volume to integer
    pdf['Volume'] = pd.to_numeric(pdf['Volume'], errors='coerce').astype('Int64')

    # Convert Date to datetime if it's not already
    pdf['Date'] = pd.to_datetime(pdf['Date'])

    # Set Date as index
    pdf.set_index('Date', inplace=True)

    # Create an empty dictionary to store DataFrames for each ticker
    ticker_dfs = {}

    # Group data by ticker
    for ticker in pdf['Ticker'].unique():
        # Filter data for current ticker
        ticker_data = pdf[pdf['Ticker'] == ticker].copy()

        # Drop the Ticker column as it's no longer needed
        ticker_data = ticker_data.drop('Ticker', axis=1)

        # Store the DataFrame in the dictionary
        ticker_dfs[ticker] = ticker_data

    # Combine all DataFrames with multi-level columns
    if len(ticker_dfs) == 1:
        # Single ticker case
        ticker = list(ticker_dfs.keys())[0]
        result = ticker_dfs[ticker]
    else:
        # Multiple tickers case
        result = pd.concat(ticker_dfs.values(), axis=1, keys=ticker_dfs.keys())

        # Reorder levels to match yfinance format (ticker, then metric)
        result = result.reorder_levels([1, 0], axis=1)

    return result
