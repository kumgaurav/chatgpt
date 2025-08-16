import sys
import yfinance as yf
import pandas as pd
import numpy as np
from pyspark.sql import Window
from pyspark.sql.functions import col, to_timestamp, to_date, lit, row_number, max, current_timestamp, desc
from pyspark.sql.types import StructType, StructField, StringType, DoubleType, TimestampType, DateType
from datetime import datetime, timedelta, date
import os
import logging
from typing import Optional, List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time
import argparse

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Import common utilities for config and Spark initialization from new utils
import importlib.util

UTILITY_DIR = os.path.join(os.path.dirname(__file__), 'utility')

def _import_module_from_path(module_name: str, file_name: str):
    module_path = os.path.join(UTILITY_DIR, file_name)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module {module_name} from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

utils_config = _import_module_from_path('utils_config', 'utils_config.py')
utils_spark = _import_module_from_path('utils_spark', 'utils_spark.py')
utils_data = _import_module_from_path('utils_data', 'utils_data.py')
utils_earnings = _import_module_from_path('utils_earnings', 'utils_earnings.py')
yfinance_utils = _import_module_from_path('yfinance_utils', 'yfinance_utils.py')
data_utility = _import_module_from_path('data_utility', 'data_utility.py')


def main():
    """
    Main function that orchestrates the stock data loading process.
    
    This function performs the following steps:
    1. Initialize Spark session and load configuration
    2. Determine ticker list source (config file vs database)
    3. Set appropriate date ranges based on data source
    4. Download fresh stock data from Yahoo Finance
    5. Process and transform the data
    6. Calculate price changes and technical indicators
    7. Filter out duplicate records
    8. Load new data into MySQL database
    9. Clean up resources
    """
    print(f"🚀 Running stock batch loader...")
    
    # ================================
    # CONFIGURATION + SPARK (via common utils)
    # ================================
    config = utils_config.load_config(None)
    print("📋 Configuration sections:", config.sections())
    spark = utils_spark.build_spark('Stock Loader', config)
    
    # Database connection parameters
    mysql_settings = utils_config.get_mysql_settings(
        config,
        host=None,
        port=3306,
        database=None,
        url=None,
        username=None,
        password=None,
        driver=None,
    )
    sql_driver = mysql_settings['driver']
    url = mysql_settings['url']
    table = config.get('mysql', 'table')  # Main stock data table
    stock_change_tracker_table = "stock_change_tracker"  # Table for tracking active stocks
    username = mysql_settings['user'] or ''
    password = mysql_settings['password'] or ''
    
    # ================================
    # TICKER SYMBOL MANAGEMENT
    # ================================
    
    # Resolve tickers from config or database via utility
    ticker_list, source = utils_data.get_ticker_list(
        config, spark, url, sql_driver, username, password, stock_change_tracker_table
    )

    # Load existing stock data from database (needed for date-source logic)
    print("📊 Loading existing stock data from database...")
    stockdf = spark.read.format("jdbc").options(
        url=url,
        driver=sql_driver,
        dbtable=table,
        user=username,
        password=password
    ).load()

    # Determine start_date based on source
    if source == 'db':
        if not stockdf.head(1):
            start_date = (date.today() - timedelta(days=370)).strftime("%Y-%m-%d")
            print("📅 Database is empty, using 30-day lookback")
        else:
            max_start_date = stockdf.agg(max("Date")).collect()[0][0]
            if max_start_date and str(max_start_date) < date.today().strftime("%Y-%m-%d"):
                start_date = str(max_start_date)
            else:
                start_date = (date.today() - timedelta(days=7)).strftime("%Y-%m-%d")
            print(f"📅 Using database ticker list with incremental start_date: {start_date}")
    else:
        print("📈 Using config ticker list...")
        start_date = "2025-01-01"
        print(f"📅 Using config ticker list with fixed start_date: {start_date}")
    
    #print(f"🎯 Processing {len(ticker_list)} tickers: {ticker_list}")
    print(f"🎯 Processing {len(ticker_list)} tickers")
    tickers_to_fetch = utils_earnings.get_tickers_needing_earnings_update(
        config, spark, url, sql_driver, username, password, ticker_list, 'last_stock_earnings'
    )
    test_tickers = tickers_to_fetch[:1]
    
    if test_tickers:
        print(f"📝 Need to fetch earnings for {len(test_tickers)} tickers; first batch: {test_tickers}")
    else:
        print("📝 Need to fetch earnings for 0 tickers")
    
    # ================================
    # DATE RANGE SETUP
    # ================================
    
    # Always use today's date as end date to get the most recent data
    today = date.today()
    end_date = today.strftime("%Y-%m-%d")
    
    # Fix potential issue with future dates from database
    if start_date > today.strftime("%Y-%m-%d"):
        # If start_date is in the future, use a recent past date
        start_date = (today - timedelta(days=365)).strftime("%Y-%m-%d")
        print(f"⚠️ Adjusted start_date to prevent future date issues: {start_date}")
    
    print(f"📅 Date range: {start_date} to {end_date}")
    print(f"🔎 Fetching yfinance datasets for: {test_tickers} on {start_date}")
    dataframes = yfinance_utils.get_yfinance_dataframes(test_tickers, start_date)
    stock_earnings_df = dataframes['earnings']
    earnings_estimate_df = dataframes['earnings_estimate']
    earnings_history_df = dataframes['earnings_history']
    quarterly_revenue_df = dataframes['quarterly_revenue']
    growth_estimates_df = dataframes['growth_estimates']
    revenue_estimates_df = dataframes['revenue_estimates']
    quarterly_income_df = dataframes['quarterly_income']
    stock_details_df = dataframes['stock_details']
    counts = {k: len(v) for k, v in dataframes.items()}
    print(f"✅ Pulled datasets: {counts}")
    print(f"🔎 earnings_df sample:\n{stock_earnings_df.head().to_string(index=False) if not stock_earnings_df.empty else 'EMPTY'}")
    print(f"🔎 earnings_estimate_df sample:\n{earnings_estimate_df.head().to_string(index=False) if not earnings_estimate_df.empty else 'EMPTY'}")

    # ================================
    # WRITE BACK MISSING ROWS PER TABLE
    # ================================
    wrote = {}
    print("💾 Writing stock_details missing rows (by Symbol)...")
    wrote['stock_details'] = data_utility.write_stock_details(
        spark, data_utility.pandas_to_spark(spark, stock_details_df), url, sql_driver, username, password
    )
    print("💾 Writing stock_earnings missing rows (by ticker, earnings_date)...")
    wrote['stock_earnings'] = data_utility.write_stock_earnings(
        spark, data_utility.pandas_to_spark(spark, stock_earnings_df), url, sql_driver, username, password
    )
    print("💾 Writing stock_earnings missing rows (by ticker, earnings_date)...")
    wrote['last_stock_earnings'] = data_utility.write_last_stock_earnings(
        spark, data_utility.pandas_to_spark(spark, stock_earnings_df), url, sql_driver, username, password
    )
    print("💾 Writing earnings_estimates missing rows (by ticker, earnings_date)...")
    wrote['earnings_estimate'] = data_utility.write_earnings_estimates(
        spark, data_utility.pandas_to_spark(spark, earnings_estimate_df), url, sql_driver, username, password
    )
    print("💾 Writing earnings_history missing rows (by ticker, earnings_date)...")
    wrote['earnings_history'] = data_utility.write_earnings_history(
        spark, data_utility.pandas_to_spark(spark, earnings_history_df), url, sql_driver, username, password
    )
    print("💾 Writing quarterly_revenue missing rows (by ticker, report_date)...")
    wrote['quarterly_revenue'] = data_utility.write_quarterly_revenue(
        spark, data_utility.pandas_to_spark(spark, quarterly_revenue_df), url, sql_driver, username, password
    )
    print("💾 Writing growth_estimates missing rows (by ticker, estimate_fetch_date)...")
    wrote['growth_estimates'] = data_utility.write_growth_estimates(
        spark, data_utility.pandas_to_spark(spark, growth_estimates_df), url, sql_driver, username, password
    )
    print("💾 Writing revenue_estimates missing rows (by ticker, next_earnings_date)...")
    wrote['revenue_estimates'] = data_utility.write_revenue_estimates(
        spark, data_utility.pandas_to_spark(spark, revenue_estimates_df), url, sql_driver, username, password
    )
    print("💾 Writing quarterly_income missing rows (by ticker, report_date)...")
    wrote['quarterly_income'] = data_utility.write_quarterly_income(
        spark, data_utility.pandas_to_spark(spark, quarterly_income_df), url, sql_driver, username, password
    )
    print(f"💾 Rows written per table (missing-only appends): {wrote}")
    


# ================================
# ENTRY POINT
# ================================

if __name__ == "__main__":
    main()
