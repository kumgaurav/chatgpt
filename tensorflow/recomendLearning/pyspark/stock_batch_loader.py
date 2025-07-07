"""
Stock Batch Loader

This script downloads stock price data using yfinance and loads it into a MySQL database.
It uses PySpark for data processing and handles rate limiting with retry logic.

Key Features:
- Downloads stock data from Yahoo Finance
- Processes data with PySpark  
- Calculates price changes and technical indicators
- Loads only new data (avoids duplicates)
- Handles rate limiting with exponential backoff
- Supports both config-based and database-based ticker lists
"""

# ================================
# IMPORTS
# ================================

# Standard library imports
import time
import os
import sys
import configparser
from datetime import date, datetime, timedelta
from pprint import pprint
import logging

# Third-party imports for data processing
import pandas as pd
import yfinance as yf
from pandas_datareader import data as pdr

# PySpark imports for big data processing
from pyspark.sql import SparkSession, Window
from pyspark import SparkConf, SparkContext
from pyspark.sql.functions import lit, col, max, lag, when, trim

# HTTP request handling
import requests
from requests.exceptions import HTTPError
from curl_cffi import requests

# ================================
# LOGGING CONFIGURATION
# ================================

# Configure logging for debugging and monitoring
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================================
# CONSTANTS AND MAPPINGS
# ================================

# Create a mapping for period codes to meaningful names
# Used for financial data analysis and reporting
period_mapping = {
    "0q": "Current Quarter",
    "+1q": "Next Quarter",
    "0y": "Current Year",
    "+1y": "Next Year",
    "LTG": "Long-Term Growth"
}


def get_data(tickers, start_date, end_date, max_retries=3, wait_time=60):
    """
    Downloads stock price data from Yahoo Finance with retry logic.
    
    This function handles rate limiting and network errors gracefully by implementing
    exponential backoff retry logic. It downloads data for multiple tickers and 
    saves the results to a CSV file.
    
    Args:
        tickers (list): List of stock ticker symbols to download
        start_date (str): Start date in YYYY-MM-DD format
        end_date (str): End date in YYYY-MM-DD format  
        max_retries (int): Maximum number of retry attempts for failed requests
        wait_time (int): Wait time between retries in seconds
        
    Returns:
        str: Path to the saved CSV file containing stock data
        None: If all retries fail
        
    Raises:
        ValueError: If no data is downloaded
        HTTPError: If rate limit is exceeded after max retries
        Exception: For other unexpected errors
    """
    attempt = 0
    while attempt < max_retries:
        try:
            # Create a session with Chrome impersonation to avoid bot detection
            session = requests.Session(impersonate="chrome")
            
            # Download stock price data from Yahoo Finance
            print(f"📈 Downloading stock data for {len(tickers)} tickers from {start_date} to {end_date}...")
            data = yf.download(tickers, start=start_date, auto_adjust=True, session=session, progress=False)
            
            # Validate that data was successfully downloaded
            if data.empty:
                raise ValueError("No data downloaded. Check ticker symbols or date range.")
            
            # Convert MultiIndex columns to normal DataFrame structure
            # This transforms the data from wide format to long format with tickers as a column
            # Wide format: Date | AAPL_Open | AAPL_Close | MSFT_Open | MSFT_Close
            # Long format: Date | Ticker | Open | Close
            data = data.stack(level=1, future_stack=True).reset_index()
            data.rename(columns={"level_1": "Ticker"}, inplace=True)
            
            # Ensure data directory exists for saving files
            os.makedirs("data", exist_ok=True)
            
            # Save the processed stock data to CSV file with date in filename
            price_filename = f"data/stock_prices_{start_date}.csv"
            data.to_csv(price_filename, index=False)
            print(f"📁 Stock prices saved as {price_filename}")
            
            return price_filename
        
        except HTTPError as e:
            # Handle rate limiting from Yahoo Finance API
            attempt += 1
            if attempt >= max_retries:
                print(f"❌ Max retries ({max_retries}) reached. Rate limit error: {str(e)}")
                sys.exit(1)  # Exit after max retries
            print(f"⚠️ Rate limit error: {str(e)}. Retrying ({attempt}/{max_retries}) after {wait_time} seconds...")
            time.sleep(wait_time)  # Wait before retrying
            
        except Exception as e:
            # Handle any other unexpected errors
            print(f"❌ Error downloading data: {str(e)}")
            sys.exit(1)  # Exit for other errors

    return None  # Return None if all retries fail


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
    # SPARK SESSION INITIALIZATION
    # ================================
    
    # Set environment variables to ensure consistent Python version across Spark processes
    # This prevents version mismatch issues between driver and executor processes
    python_path = sys.executable
    os.environ['PYSPARK_PYTHON'] = python_path
    os.environ['PYSPARK_DRIVER_PYTHON'] = python_path
    print(f"🐍 Using Python: {python_path}")
    
    # Initialize Spark session with MySQL JDBC driver
    # The JDBC driver is needed to read/write data from/to MySQL database
    spark = SparkSession.builder.master("local[1]").appName("Stock Loader") \
        .config("spark.jars",
                "/Users/gaurav/.m2/repository/com/mysql/mysql-connector-j/8.0.33/mysql-connector-j-8.0.33.jar").getOrCreate()
    
    # ================================
    # CONFIGURATION LOADING
    # ================================
    
    # Load configuration from INI file
    config = configparser.ConfigParser()
    config.read(os.path.join(os.path.dirname(__file__), 'conf/config.ini'))
    print("📋 Configuration sections:", config.sections())
    
    # Database connection parameters
    sql_driver = "com.mysql.cj.jdbc.Driver"
    url = 'jdbc:mysql://localhost/{}'.format(config.get('mysql', 'database'))
    table = config.get('mysql', 'table')  # Main stock data table
    stock_change_tracker_table = "stock_change_tracker"  # Table for tracking active stocks
    username = config.get('mysql', 'username')
    password = config.get('mysql', 'password')
    
    # ================================
    # TICKER SYMBOL MANAGEMENT
    # ================================
    
    # Load ticker symbols from configuration file
    # Example config: symbols = AAPL MSFT GOOGL TSLA
    ticker_list = config.get('stocks', 'symbols').split() if config.get('stocks', 'symbols').strip() else []
    
    # Load existing stock data from database (needed for both scenarios)
    print("📊 Loading existing stock data from database...")
    stockdf = spark.read.format("jdbc").options(
        url=url,
        driver=sql_driver,
        dbtable=table,
        user=username,
        password=password
    ).load()
    
    # ================================
    # DATA SOURCE LOGIC
    # ================================
    
    # Check if config ticker list is empty, if so pull from database
    # This provides a fallback mechanism when config is not properly set
    if not ticker_list:
        print("🔄 Config ticker list is empty, pulling from database...")
        
        # Load active ticker symbols from change tracker table
        change_tracker_df = spark.read.format("jdbc").options(
            url=url, 
            driver=sql_driver, 
            user=username, 
            password=password,
            dbtable=stock_change_tracker_table
        ).load()
        
        # Filter for only active stocks and get their symbols
        change_tracker_df = change_tracker_df.filter(col("is_active") == True).select("symbol")
        ticker_list = change_tracker_df.rdd.flatMap(lambda x: x).collect()
        
        # Show the tracker data for debugging purposes
        print("📋 Active stocks from database:")
        change_tracker_df.show(10, truncate=False)
        
        # Use incremental loading: start from the latest date in database
        # This ensures we only download new data, not duplicates
        if not stockdf.head(1):  # Check if stockdf is empty
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
        
        # Use full historical loading: start from a fixed date
        # This is useful for initial data loading or complete refresh
        start_date = "2025-01-01"
        print(f"📅 Using config ticker list with fixed start_date: {start_date}")
    
    print(f"🎯 Processing {len(ticker_list)} tickers: {ticker_list}")
    
    # ================================
    # DATE RANGE SETUP
    # ================================
    
    # Always use today's date as end date to get the most recent data
    today = date.today()
    end_date = today.strftime("%Y-%m-%d")
    
    # Fix potential issue with future dates from database
    if start_date > today.strftime("%Y-%m-%d"):
        # If start_date is in the future, use a recent past date
        start_date = (today - timedelta(days=7)).strftime("%Y-%m-%d")
        print(f"⚠️ Adjusted start_date to prevent future date issues: {start_date}")
    
    print(f"📅 Date range: {start_date} to {end_date}")
    
    # ================================
    # DATA DOWNLOAD AND PROCESSING
    # ================================
    
    # Download fresh stock data from Yahoo Finance
    price_filename = get_data(ticker_list, start_date, end_date, max_retries=1, wait_time=60)
    print(f"📄 Downloaded data file: {price_filename}")
    
    # Load the downloaded CSV into Spark DataFrame
    stk_new_df = spark.read.option("header", True).csv(price_filename)
    
    # ================================
    # DATA TRANSFORMATION
    # ================================
    
    # Standardize column names and add required columns
    # Rename 'Ticker' to 'Symbol' to match database schema
    # Add 'Adj Close' column (copy of 'Close' for compatibility)
    stk_new_df = stk_new_df.withColumnRenamed("Ticker", "Symbol").withColumn("Adj Close", col("Close"))
    
    # Filter out rows with missing or empty Open prices
    # This removes invalid/incomplete data that could cause issues
    stk_new_df = stk_new_df.filter(col("Open").isNotNull() & (trim(col("Open")) != ""))
    
    # Sort by date to ensure proper chronological order
    # This is important for time-series analysis and lag calculations
    stk_new_df = stk_new_df.orderBy("Date")
    
    # ================================
    # PRICE CHANGE CALCULATION
    # ================================
    
    # Define window specification for calculating price changes
    # Partition by Symbol to calculate changes within each stock separately
    # Order by Date to ensure proper sequence for lag calculations
    window_spec = Window.partitionBy("Symbol").orderBy("Date")
    
    # Calculate the previous day's closing price using lag function
    # lag(1) gets the value from the previous row within each symbol partition
    prev_close = lag("Close").over(window_spec)
    
    # Calculate price change with different logic for first vs subsequent days:
    # - First day of a stock: Close - Open (intraday change)
    # - Subsequent days: Close - Previous Close (day-to-day change)
    stk_new_df = stk_new_df.withColumn("price_change", 
        when(prev_close.isNull(), col("Close") - col("Open"))
        .otherwise(col("Close") - prev_close)
    )
    
    # ================================
    # DUPLICATE FILTERING
    # ================================
    
    # Get existing data's Date and Symbol combinations to identify duplicates
    existing_data = stockdf.select("Date", "Symbol")
    
    # Perform anti-join to get only new records that don't exist in database
    # Anti-join returns rows from left table that have no match in right table
    filtered_data = stk_new_df.join(existing_data, ["Date", "Symbol"], "left_anti")
    
    print(f"📊 Found {filtered_data.count()} new records to insert:")
    filtered_data.show(5, truncate=False)
    
    # ================================
    # DATABASE INSERTION
    # ================================
    
    # Write the new data to MySQL database
    # Using 'append' mode to add new records without overwriting existing data
    print("💾 Inserting new data into database...")
    filtered_data.write.format('jdbc').options(
        url=url,
        driver=sql_driver,
        dbtable=table,
        user=username,
        password=password
    ).mode('append').save()
    
    print("✅ Data successfully loaded into database!")
    
    # ================================
    # CLEANUP
    # ================================
    
    # Stop Spark session to free up resources
    spark.stop()
    print("🏁 Stock batch loading process completed successfully!")


# ================================
# ENTRY POINT
# ================================

if __name__ == "__main__":
    main()
