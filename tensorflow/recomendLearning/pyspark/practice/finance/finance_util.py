import pandas as pd
import numpy as np


def calculate_technical_indicators(df):
    """
    Calculate various technical indicators for a given stock data DataFrame.

    This function computes a set of common technical indicators used in financial
    analysis based on the stock price and volume data contained in the input DataFrame.
    The calculated indicators include:

    1. **Return**: Percentage change in closing price.
    2. **20d_MA**: 20-day moving average of the closing price.
    3. **50d_MA**: 50-day moving average of the closing price.
    4. **20d_Volatility**: 20-day rolling standard deviation of daily returns.
    5. **20d_SD**: 20-day rolling standard deviation of closing prices.
    6. **Upper_BB**: Upper Bollinger Band, calculated as 20-day moving average + 2 standard deviations.
    7. **Lower_BB**: Lower Bollinger Band, calculated as 20-day moving average - 2 standard deviations.
    8. **RSI**: 14-day Relative Strength Index (RSI), a momentum oscillator.
    9. **12d_EMA**: 12-day Exponential Moving Average (EMA) of the closing price.
    10. **26d_EMA**: 26-day Exponential Moving Average (EMA) of the closing price.
    11. **MACD**: Difference between the 12-day and 26-day EMA (Moving Average Convergence Divergence).
    12. **Signal_Line**: 9-day EMA of the MACD.
    13. **OBV**: On-Balance Volume, which uses price and volume to indicate buying and selling pressure.
    14. **TR**: True Range, the greatest of (High - Low), (High - Close_previous), and (Low - Close_previous).
    15. **ATR**: 14-day Average True Range (ATR), used to measure volatility.
    16. **Williams_%R**: Williams %R, a momentum indicator that measures overbought and oversold levels.

    Args:
        df (pandas.DataFrame): A DataFrame containing historical stock data with columns:
            - 'Close': Closing price.
            - 'Volume': Trading volume.
            - 'High': Highest price during the day.
            - 'Low': Lowest price during the day.

    Returns:
        pandas.DataFrame: The original DataFrame with additional columns for each of the calculated
        technical indicators.

    Notes:
        - The function assumes that the input DataFrame has been preprocessed and has the necessary columns.
        - It is recommended to call this function on a DataFrame with data sorted by date in ascending order.
    """
    df['Return'] = df['Close'].pct_change() * 100
    df['20d_MA'] = df['Close'].rolling(window=20).mean()
    df['50d_MA'] = df['Close'].rolling(window=50).mean()
    df['20d_Volatility'] = df['Return'].rolling(window=20).std()
    df['20d_MA'] = df['Close'].rolling(window=20).mean()
    df['20d_SD'] = df['Close'].rolling(window=20).std()
    df['Upper_BB'] = df['20d_MA'] + (df['20d_SD'] * 2)
    df['Lower_BB'] = df['20d_MA'] - (df['20d_SD'] * 2)
    delta = df['Close'].diff(1)
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    RS = gain / loss
    df['RSI'] = 100 - (100 / (1 + RS))
    df['12d_EMA'] = df['Close'].ewm(span=12, adjust=False).mean()
    df['26d_EMA'] = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = df['12d_EMA'] - df['26d_EMA']
    df['Signal_Line'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['OBV'] = (np.sign(df['Close'].diff()) * df['Volume']).fillna(0).cumsum()
    high_low = df['High'] - df['Low']
    high_close = np.abs(df['High'] - df['Close'].shift())
    low_close = np.abs(df['Low'] - df['Close'].shift())
    df['TR'] = high_low.combine(high_close, np.maximum).combine(low_close, np.maximum)
    df['ATR'] = df['TR'].rolling(window=14).mean()
    highest_high = df['High'].rolling(window=14).max()
    lowest_low = df['Low'].rolling(window=14).min()
    df['Williams_%R'] = (highest_high - df['Close']) / (highest_high - lowest_low) * -100
    return df
