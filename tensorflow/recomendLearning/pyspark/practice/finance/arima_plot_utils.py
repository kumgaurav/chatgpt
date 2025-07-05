# print(df_with_arima.info())
from pandas.plotting import autocorrelation_plot
import matplotlib.pyplot as plt


def autocorrelation_plot_util(series):
    autocorrelation_plot(series)
    plt.figure(figsize=(10, 7), dpi=80)
    plt.show()


def plot_real_vs_predicted(test, predictions):
    plt.figure(figsize=(12, 6), dpi=100)
    plt.plot(test, label='Real')
    plt.plot(predictions, color='red', label='Predicted')
    plt.xlabel('Days')
    plt.ylabel('USD')
    plt.title('Figure 5: ARIMA model on GS stock')
    plt.legend()
    plt.show()
