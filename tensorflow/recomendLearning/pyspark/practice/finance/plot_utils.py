import matplotlib.pyplot as plt
import numpy as np
from collections import deque

def plot_technical_indicators(dataset, last_days):
    plt.figure(figsize=(16, 10), dpi=100)
    shape_0 = dataset.shape[0]
    xmacd_ = shape_0 - last_days

    dataset = dataset.iloc[-last_days:, :]
    x_ = range(3, dataset.shape[0])
    x_ = list(dataset.index)

    # Plot first subplot
    plt.subplot(2, 1, 1)
    plt.plot(dataset['MA7'], label='MA 7', color='g', linestyle='--')
    plt.plot(dataset['Close'], label='Closing Price', color='b')
    plt.plot(dataset['MA21'], label='MA 21', color='r', linestyle='--')
    plt.plot(dataset['BB_upper'], label='Upper Band', color='c')
    plt.plot(dataset['BB_lower'], label='Lower Band', color='c')
    plt.fill_between(x_, dataset['BB_lower'], dataset['BB_upper'], alpha=0.35)
    plt.title('Technical indicators for Goldman Sachs - last {} days.'.format(last_days))
    plt.ylabel('USD')
    plt.legend()

    # Plot second subplot
    plt.subplot(2, 1, 2)
    plt.title('MACD')
    plt.plot(dataset['MACD'], label='MACD', linestyle='-.')
    plt.hlines(15, xmacd_, shape_0, colors='g', linestyles='--')
    plt.hlines(-15, xmacd_, shape_0, colors='g', linestyles='--')
    plt.plot(dataset['log_momentum'], label='Momentum', color='b', linestyle='-')

    plt.legend()
    plt.show()


def plot_fourier_transform_with_components(fft_df, df_with_fourier, ticker):
    fft_list = np.asarray(fft_df['fft'].tolist())
    plt.figure(figsize=(14, 7), dpi=100)
    for num_ in [3, 6, 9, 100]:
        fft_list_m10 = np.copy(fft_list)
        fft_list_m10[num_:-num_] = 0
        plt.plot(np.real(np.fft.ifft(fft_list_m10)), label=f'Fourier transform with {num_} components')

    plt.plot(df_with_fourier['Close'], label='Real')
    plt.xlabel('Days')
    plt.ylabel('USD')
    plt.title(f'Figure 3: {ticker} (close) stock prices & Fourier transforms')
    plt.legend()
    plt.show()


def plot_components_of_fourier(fft_df):
    items = deque(np.asarray(fft_df['absolute'].tolist()))
    items.rotate(int(np.floor(len(fft_df) / 2)))
    plt.figure(figsize=(10, 7), dpi=80)
    plt.stem(items)
    plt.title('Figure 4: Components of Fourier transforms')
    plt.show()
