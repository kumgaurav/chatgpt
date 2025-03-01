from statsmodels.tsa.arima.model import ARIMA
from sklearn.metrics import mean_squared_error
import numpy as np
from pandas import DataFrame
from datetime import datetime

def arima__model_data_prepare(data):
    df_with_fourier = data.copy()
    series = df_with_fourier['Close']
    model = ARIMA(series, order=(5, 1, 0))
    model_fit = model.fit()
    print(model_fit.summary())
    X = series.values  # df["close"]
    # df_with_arima = df_with_fourier.copy()
    # print(df_with_arima.info())
    size = int(len(X) * 0.66)
    train, test = X[0:size], X[size:len(X)]
    return train, test


def arima_model_predict(train, test):
    history = [x for x in train]
    predictions = []
    for t in range(len(test)):
        model = ARIMA(history, order=(5, 1, 0))
        model_fit = model.fit()
        output = model_fit.forecast()
        yhat = output[0]
        predictions.append(yhat)
        obs = test[t]
        history.append(obs)
    error = mean_squared_error(test, predictions)
    print('Test MSE: %.3f' % error)
    return predictions

def append_arima_prediction(predictions, data):
    df_with_fourier = data.copy()
    # Ensure predictions and df_with_arima have the same length
    if len(predictions) != len(df_with_fourier):
        predictions = [np.nan] * (len(df_with_fourier) - len(predictions)) + predictions

    # Add predictions as a new feature
    df_with_fourier['arima_predictions'] = predictions

    # Use the recommended ffill() method instead
    df_with_fourier['arima_predictions'] = df_with_fourier['arima_predictions'].ffill()
    return df_with_fourier
