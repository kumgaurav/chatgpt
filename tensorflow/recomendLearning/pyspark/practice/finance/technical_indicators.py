import pandas as pd
import numpy as np
from scipy.fftpack import fft

def get_technical_indicators(data):
    # Make sure we're working with a copy of the data
    df = data.copy()

    # Calculate log_momentum
    df['log_momentum'] = np.log(df['Close'] / df['Close'].shift(1))
    # Simple Moving Average (SMA)
    df['SMA20'] = df['Close'].rolling(window=20).mean()
    df['SMA50'] = df['Close'].rolling(window=50).mean()
    # Add 7-day Moving Average (MA7)
    df['MA7'] = df['Close'].rolling(window=7).mean()
    # Add 21-day Moving Average (MA21)
    df['MA21'] = df['Close'].rolling(window=21).mean()

    # Relative Strength Index (RSI)
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))

    # Moving Average Convergence Divergence (MACD)
    df['EMA12'] = df['Close'].ewm(span=12, adjust=False).mean()
    df['EMA26'] = df['Close'].ewm(span=26, adjust=False).mean()
    df['EMA7'] = df['Close'].ewm(span=7, adjust=False).mean()
    df['EMA21'] = df['Close'].ewm(span=21, adjust=False).mean()
    # df['MACD'] = df['EMA12'] - df['EMA26']
    df['MACD'] = df['EMA7'] - df['EMA21']
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()

    # Bollinger Bands
    df['BB_middle'] = df['Close'].rolling(window=20).mean()
    df['BB_upper'] = df['BB_middle'] + 2 * df['Close'].rolling(window=20).std()
    df['BB_lower'] = df['BB_middle'] - 2 * df['Close'].rolling(window=20).std()

    # Average True Range (ATR)
    high_low = df['High'] - df['Low']
    high_close = np.abs(df['High'] - df['Close'].shift())
    low_close = np.abs(df['Low'] - df['Close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    df['ATR'] = true_range.rolling(14).mean()

    return df

def get_fourier_components(data):
    # Make sure we're working with a copy of the data
    df_with_fourier = data.copy() #data[['Date', 'Close']].copy()

    prices = df_with_fourier['Close'].values
    fft_result = fft(prices)
    close_fft = np.fft.fft(np.asarray(df_with_fourier['Close'].tolist()))
    fft_df = pd.DataFrame({'fft': close_fft})

    print("Length of df_with_fourier:", len(df_with_fourier))
    print("Length of fft_df:", len(fft_df))
    print("Number of rows in df_with_fourier:", df_with_fourier.shape[0])
    print("Number of rows in fft_df:", fft_df.shape[0])

    # Calculate magnitude and phase
    absolute = np.abs(fft_result)
    angle = np.angle(fft_result)

    # Add new columns using .loc[]
    fft_df.loc[:, 'absolute'] = absolute
    fft_df.loc[:, 'angle'] = angle
    fft_df.loc[:, 'frequency'] = np.fft.fftfreq(len(prices))[:len(prices)]

    # print(df.head())
    print("Length of prices array:", len(prices))
    print("Length of fft_result:", len(fft_result))
    print("Length of close_fft list:", len(close_fft))
    fft_list = np.asarray(fft_df['fft'].tolist())
    # Assuming fft_df is already sorted by magnitude
    n_components = 5  # Number of top components to add

    for i in range(n_components):
        component = np.zeros_like(fft_list)
        component[i] = fft_list[i]
        component[-i] = fft_list[-i]
        inverse_fft = np.real(np.fft.ifft(component))
        df_with_fourier[f'fourier_component_{i + 1}'] = inverse_fft

    # Now df_with_fourier has new columns for each Fourier component
    # print(df_with_fourier.head())
    return df_with_fourier, fft_df

