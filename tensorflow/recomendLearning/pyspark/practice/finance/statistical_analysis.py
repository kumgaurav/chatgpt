from arch import arch_model
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
from sklearn.impute import SimpleImputer
from statsmodels.stats.outliers_influence import variance_inflation_factor
from numpy.linalg import svd
from statsmodels.stats.stattools import durbin_watson
from statsmodels.tsa.stattools import acf, pacf
import pandas as pd
import numpy as np

def heteroskedasticity_analysis(data):
    df_with_stats = data.copy()
    window_size = 20  # You can adjust this based on your data

    # Calculate rolling standard deviation
    df_with_stats['heteroskedasticity'] = df_with_stats['Close'].rolling(window=window_size).std()
    return df_with_stats


def garch_volatility(returns, p=1, q=1):
    # Rescale the returns as suggested
    scaled_returns = returns * 100
    model = arch_model(scaled_returns, vol='GARCH', p=p, q=q)
    results = model.fit(disp='off')
    return results.conditional_volatility / 100  # Scale back to original


def garch_volatility_analysis(data):
    df_with_stats = data.copy()
    # Assuming df_with_arima is your DataFrame and 'Close' is your price column
    returns = df_with_stats['Close'].pct_change().dropna()
    df_with_stats['garch_volatility'] = garch_volatility(returns)
    return df_with_stats


def rolling_volatility(series, window=20):
    return np.log(series).diff().rolling(window=window).std()


def rolling_volatility_analysis(data):
    df_with_stats = data.copy()
    # Assuming df_with_arima is your DataFrame
    df_with_stats['rolling_volatility'] = rolling_volatility(df_with_stats['Close'])
    return df_with_stats


def average_correlation_analysis(data):
    df_with_stats = data.copy()
    numeric_columns = df_with_stats.select_dtypes(include=[np.number]).columns
    correlation_matrix = df_with_stats[numeric_columns].corr()
    # Calculate average correlation for each feature
    avg_correlations = correlation_matrix.mean()
    # print(avg_correlations)
    # print("df_with_stats.index : ", df_with_stats.index)
    # print("avg_correlations.index : ", avg_correlations.index)
    # print("type(df_with_stats.index) : ", type(df_with_stats.index))
    # print("type(avg_correlations.index): ", type(avg_correlations.index))
    avg_corr_dict = avg_correlations.to_dict()
    avg_corr_df = pd.DataFrame(
        {f'{col}_avg_corr': [avg_corr_dict[col]] * len(df_with_stats) for col in df_with_stats.columns if
         col in avg_corr_dict})
    df_with_stats = pd.concat([df_with_stats, avg_corr_df], axis=1)
    return df_with_stats


def variance_inflation_analysis(data):
    # Create a copy of the original data for manipulation
    df_with_stats_tmp = data.copy()
    # df_with_stats_original = data.copy()

    # Select only numeric columns (excluding categorical ones like 'Date' and 'Ticker')
    numeric_cols = df_with_stats_tmp.select_dtypes(include=[np.number]).columns.tolist()

    # Fill missing values with column median instead of dropping rows
    df_vif = df_with_stats_tmp[numeric_cols].copy()
    df_vif = df_vif.fillna(df_vif.median())  # Replace NaNs with median

    # Drop constant columns (zero variance)
    df_vif = df_vif.loc[:, df_vif.nunique() > 1]

    # Check if dataset is still valid for VIF calculation
    if df_vif.shape[1] < 2:
        print("Not enough valid columns for VIF calculation after cleaning.")
    else:
        # Step 1: Identify Highly Correlated Features (Threshold: 0.99)
        correlation_matrix = df_vif.corr().abs()
        high_corr_vars = np.where(correlation_matrix >= 0.99)
        high_corr_vars = [(df_vif.columns[x], df_vif.columns[y]) for x, y in zip(*high_corr_vars) if x != y]

        if high_corr_vars:
            # print("Highly correlated features (correlation >= 0.99):")
            # print(high_corr_vars)

            # Drop one of each correlated pair
            to_drop = set(x[1] for x in high_corr_vars)  # Drop the second feature in each pair
            df_vif = df_vif.drop(columns=to_drop)

        # Step 2: Iteratively Remove Features with High VIF
        def calculate_vif(df):
            vif_data = pd.DataFrame()
            vif_data["Feature"] = df.columns
            vif_data["VIF"] = [variance_inflation_factor(df.values, i) for i in range(df.shape[1])]
            return vif_data

        vif_threshold = 10  # Common cutoff for multicollinearity
        vif_data = calculate_vif(df_vif)

        while vif_data["VIF"].max() > vif_threshold:
            highest_vif_feature = vif_data.loc[vif_data["VIF"].idxmax(), "Feature"]
            # print(f"Dropping {highest_vif_feature} due to high VIF: {vif_data['VIF'].max()}")
            df_vif = df_vif.drop(columns=[highest_vif_feature])

            # Recalculate VIF
            vif_data = calculate_vif(df_vif)

        # Step 3: Append VIF values to the original dataframe
        # Create a VIF dictionary where the key is the feature name and value is the VIF score
        vif_dict = dict(zip(vif_data['Feature'], vif_data['VIF']))

        # Add the VIF values to the original dataframe
        for feature in vif_dict:
            # Ensure the feature exists in the original dataframe before adding VIF
            if feature in df_with_stats_tmp.columns:
                df_with_stats_tmp[feature + '_VIF'] = vif_dict[feature]

        # Display the dataframe with the new VIF columns
        #print("\nUpdated dataframe with VIF values:")
        #print(df_with_stats_tmp.head())

    return df_with_stats_tmp

def condition_number(X):
    singular_values = svd(X, compute_uv=False)
    return singular_values[0] / singular_values[-1]

def condition_number_analysis(data):
    df_with_stats = data.copy()
    numeric_df = df_with_stats.select_dtypes(include=[np.number])
    # print(numeric_df)
    clean_df = numeric_df.replace([np.inf, -np.inf], np.nan).dropna()
    if not clean_df.empty:
        df_with_stats['condition_number'] = condition_number(clean_df)
    else:
        print("No valid data after cleaning. Skipping condition number calculation.")
    return df_with_stats


def serial_correlation_analysis(data):
    df_with_stats = data.copy()
    # Calculate Durbin-Watson statistic
    df_with_stats['durbin_watson'] = durbin_watson(df_with_stats['Close'])

    # Calculate autocorrelation and partial autocorrelation
    lag = 5  # You can adjust this value based on your needs
    acf_values = acf(df_with_stats['Close'], nlags=lag)
    pacf_values = pacf(df_with_stats['Close'], nlags=lag)
    for i in range(1, lag + 1):
        df_with_stats[f'acf_lag_{i}'] = acf_values[i]
        df_with_stats[f'pacf_lag_{i}'] = pacf_values[i]
    return df_with_stats
