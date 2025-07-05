"""
Stock Data Loader for Yahoo Finance Integration with PySpark and MySQL

This module provides a comprehensive solution for fetching stock earnings calendar and 
quarterly financial data from Yahoo Finance, processing it with PySpark, and storing it 
in MySQL database tables. The loader uses Yahoo Finance's calendar data to extract 
earnings estimates and ranges.

Features:
    - Fetches earnings calendar data with high/low/average estimates
    - Retrieves quarterly revenue data
    - Extracts comprehensive company information and fundamentals
    - Captures ESG (Environmental, Social, Governance) sustainability metrics
    - Processes detailed quarterly financial statements (P&L, cash flow, balance sheet)
    - Supports both config-based and database-driven symbol lists
    - Implements data deduplication and filtering
    - Stores max earnings date per symbol in main table
    - Maintains complete historical data in history table
    - Stores company details in dedicated stock_details table
    - Stores ESG sustainability data in stock_sustainability table
    - Stores detailed quarterly financials in quarterly_financials table
    - Multi-threaded data fetching for 5-10x performance improvement
    - Intelligent symbol filtering to skip symbols with existing future earnings
    - Configurable thread pool size and timeout handling
    - Thread-safe data collection and error handling
    - Performance metrics and progress tracking
    - Smart optimization to avoid redundant API calls
    - Provides comprehensive logging and error handling
    - Uses PySpark for scalable data processing

Author: Kumar Gaurav
Date: January 14, 2025
Version: 2.0
License: MIT

Dependencies:
    - yfinance: Yahoo Finance API wrapper (updated for calendar data)
    - pyspark: Apache Spark Python API
    - pandas: Data manipulation library
    - mysql-connector-j: MySQL JDBC driver

Usage:
    loader = StockDataLoader()
    loader.run()
    
Configuration:
    Requires conf/config.ini with sections for mysql, stocks, and spark settings.
    
Database Tables:
    - stocks_earnings: Contains latest earnings data per symbol
    - stocks_earnings_history: Contains all historical earnings data
    - revenue table: Contains quarterly financial data
    - stock_details: Contains comprehensive company information and fundamentals
    - stock_sustainability: Contains ESG sustainability metrics and scores
    - quarterly_financials: Contains detailed quarterly financial statements
"""

import sys
import yfinance as yf
import pandas as pd
import numpy as np
from pyspark.sql import SparkSession, Window
from pyspark.sql.functions import col, to_timestamp, to_date, lit, row_number, max, current_timestamp, desc
from pyspark.sql.types import StructType, StructField, StringType, DoubleType, TimestampType, DateType
from datetime import datetime, timedelta
import configparser
import os
import logging
from typing import Optional, List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# Constants
class Constants:
    """Central constants for the StockDataLoader."""
    
    # Rate limit detection patterns
    RATE_LIMIT_PATTERNS = [
        'rate limit', 'too many requests', '429', 'rate limited',
        'quota exceeded', 'throttled', 'http 429', 'http error 429'
    ]
    
    # Default configuration values
    DEFAULT_MAX_WORKERS = 8
    DEFAULT_TIMEOUT_SECONDS = 30
    DEFAULT_SMART_FILTER_DAYS = 7
    DEFAULT_MAX_RETRIES = 3
    DEFAULT_RETRY_SLEEP_SECONDS = 60  # Increased from 30 to 60 for more conservative rate limit handling
    
    # Database table suffixes
    HISTORY_TABLE_SUFFIX = '_history'
    
    # Financial data columns
    EARNINGS_COLUMNS = [
        'earnings_high', 'earnings_low', 'earnings_average',
        'revenue_high', 'revenue_low', 'revenue_average'
    ]
    
    COMPANY_INFO_STRING_COLUMNS = [
        'Symbol', 'company_name', 'sector', 'industry', 'website',
        'business_summary', 'city', 'state', 'country', 'phone'
    ]
    
    COMPANY_INFO_NUMERIC_COLUMNS = [
        'full_time_employees', 'market_cap', 'enterprise_value',
        'trailing_pe', 'forward_pe', 'price_to_book', 'revenue_ttm',
        'gross_margins', 'profit_margins'
    ]
    
    SUSTAINABILITY_STRING_COLUMNS = ['Symbol', 'esg_performance', 'peer_group']
    SUSTAINABILITY_NUMERIC_COLUMNS = [
        'total_esg', 'environment_score', 'social_score', 'governance_score',
        'rating_year', 'rating_month', 'highest_controversy', 'peer_count',
        'peer_esg_min', 'peer_esg_avg', 'peer_esg_max'
    ]
    
    QUARTERLY_FINANCIALS_COLUMNS = [
        'total_revenue', 'cost_of_revenue', 'gross_profit', 'operating_income',
        'ebitda', 'ebit', 'net_income', 'basic_eps', 'diluted_eps',
        'operating_cash_flow', 'free_cash_flow', 'total_debt', 'total_cash'
    ]
    
    # MySQL JDBC driver
    MYSQL_DRIVER = "com.mysql.cj.jdbc.Driver"
    
    # Business summary max length
    BUSINESS_SUMMARY_MAX_LENGTH = 1000
    
    # Large number threshold for MySQL compatibility
    MYSQL_MAX_NUMBER = 1e15


class DataUtils:
    """Utility class for data processing and validation."""
    
    @staticmethod
    def is_rate_limited_error(error_message: str) -> bool:
        """Check if an error message indicates rate limiting."""
        error_lower = str(error_message).lower()
        return any(pattern in error_lower for pattern in Constants.RATE_LIMIT_PATTERNS)
    
    @staticmethod
    def safe_get_value(data_dict: Dict, key: str, default: Any = 0.0, convert_to_float: bool = False) -> Any:
        """
        Safely extract values from dictionaries with type conversion and validation.
        
        Args:
            data_dict: Source dictionary
            key: Key to extract
            default: Default value if key not found or invalid
            convert_to_float: Whether to convert to float for numeric fields
            
        Returns:
            Extracted and validated value
        """
        try:
            val = data_dict.get(key, default)
            if val is None or val == 'N/A' or val == '':
                return default
            
            if convert_to_float and default is not None:
                try:
                    float_val = float(val) if pd.notna(val) else float(default)
                    # Handle infinity and very large numbers that MySQL can't store
                    if not np.isfinite(float_val) or abs(float_val) > Constants.MYSQL_MAX_NUMBER:
                        return float(default)
                    return float_val
                except (ValueError, TypeError, OverflowError):
                    return float(default)
            
            return val
        except (ValueError, TypeError):
            return default
    
    @staticmethod
    def safe_get_nested_value(data: Dict, keys: List[str], default: Any = None, convert_to_float: bool = False) -> Any:
        """
        Safely extract nested values from dictionaries.
        
        Args:
            data: Source dictionary
            keys: List of keys for nested access
            default: Default value if path not found
            convert_to_float: Whether to convert to float
            
        Returns:
            Extracted nested value
        """
        try:
            current = data
            for key in keys:
                if isinstance(current, dict) and key in current:
                    current = current[key]
                else:
                    return default
            
            if current is None:
                return default
                
            if convert_to_float and default is not None:
                try:
                    float_val = float(current) if pd.notna(current) else float(default)
                    if not np.isfinite(float_val) or abs(float_val) > Constants.MYSQL_MAX_NUMBER:
                        return float(default)
                    return float_val
                except (ValueError, TypeError, OverflowError):
                    return float(default)
            
            return current
        except (KeyError, TypeError, AttributeError):
            return default
    
    @staticmethod
    def safe_get_financial_metric(data_series, metric_names: List[str], default: float = 0.0) -> float:
        """
        Safely extract financial metrics from pandas series with multiple fallback names.
        
        Args:
            data_series: Pandas series containing financial data
            metric_names: List of possible metric names to try
            default: Default value if no metrics found
            
        Returns:
            Financial metric value
        """
        for metric_name in metric_names:
            try:
                if metric_name in data_series.index:
                    val = data_series[metric_name]
                    if pd.notna(val):
                        float_val = float(val)
                        if np.isfinite(float_val) and abs(float_val) <= Constants.MYSQL_MAX_NUMBER:
                            return float_val
            except (KeyError, ValueError, TypeError, OverflowError):
                continue
        return float(default)


class SchemaHelper:
    """Helper class for Spark DataFrame schema definitions."""
    
    @staticmethod
    def get_earnings_schema() -> StructType:
        """Get the schema for earnings data."""
        return StructType([
            StructField("Symbol", StringType(), True),
            StructField("earnings_date", StringType(), True),
            StructField("earnings_high", DoubleType(), True),
            StructField("earnings_low", DoubleType(), True),
            StructField("earnings_average", DoubleType(), True),
            StructField("revenue_high", DoubleType(), True),
            StructField("revenue_low", DoubleType(), True),
            StructField("revenue_average", DoubleType(), True)
        ])
    
    @staticmethod
    def get_quarterly_schema() -> StructType:
        """Get the schema for quarterly data."""
        return StructType([
            StructField("Symbol", StringType(), True),
            StructField("quarter_date", StringType(), True),
            StructField("total_revenue", DoubleType(), True)
        ])
    
    @staticmethod
    def get_company_info_schema() -> StructType:
        """Get the schema for company info data."""
        return StructType([
            StructField("Symbol", StringType(), True),
            StructField("company_name", StringType(), True),
            StructField("sector", StringType(), True),
            StructField("industry", StringType(), True),
            StructField("website", StringType(), True),
            StructField("business_summary", StringType(), True),
            StructField("full_time_employees", DoubleType(), True),
            StructField("city", StringType(), True),
            StructField("state", StringType(), True),
            StructField("country", StringType(), True),
            StructField("phone", StringType(), True),
            StructField("market_cap", DoubleType(), True),
            StructField("enterprise_value", DoubleType(), True),
            StructField("trailing_pe", DoubleType(), True),
            StructField("forward_pe", DoubleType(), True),
            StructField("price_to_book", DoubleType(), True),
            StructField("revenue_ttm", DoubleType(), True),
            StructField("gross_margins", DoubleType(), True),
            StructField("profit_margins", DoubleType(), True)
        ])
    
    @staticmethod
    def get_sustainability_schema() -> StructType:
        """Get the schema for sustainability data."""
        return StructType([
            StructField("Symbol", StringType(), True),
            StructField("total_esg", DoubleType(), True),
            StructField("environment_score", DoubleType(), True),
            StructField("social_score", DoubleType(), True),
            StructField("governance_score", DoubleType(), True),
            StructField("esg_performance", StringType(), True),
            StructField("rating_year", DoubleType(), True),
            StructField("rating_month", DoubleType(), True),
            StructField("highest_controversy", DoubleType(), True),
            StructField("peer_count", DoubleType(), True),
            StructField("peer_group", StringType(), True),
            StructField("peer_esg_min", DoubleType(), True),
            StructField("peer_esg_avg", DoubleType(), True),
            StructField("peer_esg_max", DoubleType(), True)
        ])
    
    @staticmethod
    def get_quarterly_financials_schema() -> StructType:
        """Get the schema for quarterly financials data."""
        return StructType([
            StructField("Symbol", StringType(), True),
            StructField("quarter_date", StringType(), True),
            StructField("total_revenue", DoubleType(), True),
            StructField("cost_of_revenue", DoubleType(), True),
            StructField("gross_profit", DoubleType(), True),
            StructField("operating_income", DoubleType(), True),
            StructField("ebitda", DoubleType(), True),
            StructField("ebit", DoubleType(), True),
            StructField("net_income", DoubleType(), True),
            StructField("basic_eps", DoubleType(), True),
            StructField("diluted_eps", DoubleType(), True),
            StructField("operating_cash_flow", DoubleType(), True),
            StructField("free_cash_flow", DoubleType(), True),
            StructField("total_debt", DoubleType(), True),
            StructField("total_cash", DoubleType(), True)
        ])


class StockDataLoader:
    """
    A comprehensive stock data loader that fetches earnings and financial data from Yahoo Finance.
    
    This class provides functionality to:
    - Fetch earnings dates, estimates, and reported EPS values
    - Retrieve quarterly financial data including revenue
    - Process and clean the data using PySpark
    - Store data in MySQL database with proper deduplication
    - Handle both upcoming and historical earnings data
    
    The loader can work with either predefined stock symbols from configuration
    or dynamically fetch active symbols from the database.
    
    Attributes:
        config (configparser.ConfigParser): Configuration settings loaded from file
        spark (SparkSession): Active Spark session for data processing
        sql_driver (str): JDBC driver class name for MySQL connection
        url (str): JDBC connection URL for MySQL database
        username (str): Database username
        password (str): Database password
        earnings_table (str): Name of the main earnings table
        earnings_history_table (str): Name of the earnings history table
        revenue_table (str): Name of the quarterly revenue table
        stock_tracker_table (str): Name of the stock tracker table
    
    Example:
        >>> loader = StockDataLoader('conf/config.ini')
        >>> loader.run()
        
    Note:
        Requires MySQL JDBC connector JAR file to be available in the specified path.
        The configuration file must contain proper database credentials and settings.
    """
    
    def __init__(self, config_path: str = 'conf/config.ini'):
        """
        Initialize the StockDataLoader with configuration and setup connections.
        
        Args:
            config_path (str, optional): Path to the configuration file. 
                                       Defaults to 'conf/config.ini'.
        
        Raises:
            FileNotFoundError: If the configuration file doesn't exist
            Exception: If Spark session initialization fails
        """
        self.config = self._load_config(config_path)
        
        # Threading configuration (set up early)
        self.max_workers = int(self.config.get('threading', 'max_workers', fallback=str(Constants.DEFAULT_MAX_WORKERS)))
        self.timeout_seconds = int(self.config.get('threading', 'timeout_seconds', fallback=str(Constants.DEFAULT_TIMEOUT_SECONDS)))
        self.data_lock = threading.Lock()  # For thread-safe data collection
        
        # Rate limit tracking (shared across all threads)
        self.rate_limit_lock = threading.Lock()
        self.last_rate_limit_time = 0
        self.global_rate_limit_cooldown = 30  # Seconds to wait after any rate limit hit
        
        # Thread-safe data containers
        self.all_earnings_data = []
        self.all_quarterly_data = []
        self.all_company_info_data = []
        self.all_sustainability_data = []
        self.all_quarterly_financials_data = []
        
        # Initialize Spark and database connections
        self.spark = self._initialize_spark()
        self._setup_database_connection()
        
    def _load_config(self, config_path: str) -> configparser.ConfigParser:
        """
        Load configuration settings from the specified INI file.
        
        Args:
            config_path (str): Relative path to the configuration file
        
        Returns:
            configparser.ConfigParser: Loaded configuration object
            
        Raises:
            FileNotFoundError: If the configuration file doesn't exist at the specified path
        """
        config = configparser.ConfigParser()
        config_file = os.path.join(os.path.dirname(__file__), config_path)
        
        if not os.path.exists(config_file):
            raise FileNotFoundError(f"Configuration file not found: {config_file}")
            
        config.read(config_file)
        logger.info(f"Configuration loaded. Sections: {config.sections()}")
        return config
    
    def _initialize_spark(self) -> SparkSession:
        """
        Initialize and configure the Spark session with MySQL connector support.
        
        Sets up the Spark session with:
        - Local execution mode using all available cores
        - MySQL JDBC driver configuration
        - Adaptive query execution enabled
        - Partition coalescing for better performance
        
        Returns:
            SparkSession: Configured Spark session ready for use
            
        Note:
            The MySQL JAR path is read from configuration or uses a default location.
            Logging level is set to WARN to reduce verbose output.
        """
        python_path = sys.executable
        os.environ['PYSPARK_PYTHON'] = python_path
        os.environ['PYSPARK_DRIVER_PYTHON'] = python_path
        
        logger.info(f"🐍 Using Python: {python_path}")
        
        # Get MySQL connector path from config or use default
        mysql_jar_path = self.config.get('spark', 'mysql_jar_path', 
                                        fallback="/Users/gaurav/.m2/repository/com/mysql/mysql-connector-j/8.0.33/mysql-connector-j-8.0.33.jar")
        
        spark = SparkSession.builder \
            .master("local[*]") \
            .appName("Stock Data Loader") \
            .config("spark.jars", mysql_jar_path) \
            .config("spark.sql.adaptive.enabled", "true") \
            .config("spark.sql.adaptive.coalescePartitions.enabled", "true") \
            .getOrCreate()
            
        # Set log level to reduce noise
        spark.sparkContext.setLogLevel("WARN")
        return spark
    
    def _setup_database_connection(self):
        """
        Configure database connection parameters and table names from configuration.
        
        Sets up connection parameters for MySQL database including:
        - JDBC driver class
        - Database URL
        - Username and password
        - Table names for earnings, history, revenue, and stock tracker
        
        The method reads all database-related settings from the configuration file
        and prepares them for use in JDBC operations.
        """
        self.sql_driver = Constants.MYSQL_DRIVER
        self.url = f"jdbc:mysql://localhost/{self.config.get('mysql', 'database')}"
        self.username = self.config.get('mysql', 'username')
        self.password = self.config.get('mysql', 'password')
        
        # Table names
        self.earnings_table = self.config.get('mysql', 'earning_table')
        self.earnings_history_table = f"{self.earnings_table}{Constants.HISTORY_TABLE_SUFFIX}"
        self.revenue_table = self.config.get('mysql', 'revenue_table')
        self.stock_tracker_table = "stock_change_tracker"
        self.stock_details_table = "stock_details"
        self.sustainability_table = "stock_sustainability"
        self.quarterly_financials_table = "quarterly_financials"
        
        # Configuration for smart filtering and retry logic
        self.smart_filter_days = int(self.config.get('filtering', 'smart_filter_days', fallback=str(Constants.DEFAULT_SMART_FILTER_DAYS)))
        self.max_retries = int(self.config.get('retry', 'max_retries', fallback=str(Constants.DEFAULT_MAX_RETRIES)))
        self.retry_sleep_seconds = int(self.config.get('retry', 'sleep_seconds', fallback=str(Constants.DEFAULT_RETRY_SLEEP_SECONDS)))
        
        # Log configuration summary
        logger.info(f"Database URL: {self.url}")
        logger.info(f"MySQL User: {self.username}")
        logger.info(f"🧵 Threading Config: {self.max_workers} workers, {self.timeout_seconds}s timeout")
        logger.info(f"🔍 Smart Filtering: Skip symbols with future earnings (refresh old data older than {self.smart_filter_days} days)")
        logger.info(f"🔄 Retry Config: {self.max_retries} retries with {self.retry_sleep_seconds}s sleep for rate limits")
        
        # Configuration help text
        self._log_configuration_help()

    def _log_configuration_help(self):
        logger.info("🔧 Configuration Help:")
        logger.info(f"  - mysql.database: The name of the MySQL database")
        logger.info(f"  - mysql.username: The username for the MySQL database")
        logger.info(f"  - mysql.password: The password for the MySQL database")
        logger.info(f"  - mysql.earning_table: The name of the earnings table")
        logger.info(f"  - mysql.revenue_table: The name of the revenue table")
        logger.info(f"  - mysql.sustainability_table: The name of the sustainability table")
        logger.info(f"  - mysql.quarterly_financials_table: The name of the quarterly financials table")
        logger.info(f"  - filtering.smart_filter_days: Allow updates for earnings data older than N days (default: {Constants.DEFAULT_SMART_FILTER_DAYS})")
        logger.info(f"  - retry.max_retries: Maximum retry attempts for rate limit errors (default: {Constants.DEFAULT_MAX_RETRIES})")
        logger.info(f"  - retry.sleep_seconds: Sleep time between retries in seconds (default: {Constants.DEFAULT_RETRY_SLEEP_SECONDS})")
        logger.info("🔧 Configuration Help End")

    def get_ticker(self, symbol: str) -> Optional[yf.Ticker]:
        """
        Initialize and return a Yahoo Finance ticker object for the given symbol.
        
        Args:
            symbol (str): Stock symbol (e.g., 'AAPL', 'GOOGL')
        
        Returns:
            Optional[yf.Ticker]: Ticker object if successful, None if error occurs
            
        Note:
            This method handles any exceptions that might occur during ticker
            initialization and logs errors appropriately.
        """
        try:
            ticker = yf.Ticker(symbol)
            return ticker
        except Exception as e:
            logger.error(f"Error fetching ticker for {symbol}: {e}")
            return None

    def debug_calendar_structure(self, ticker: yf.Ticker, symbol: str):
        """
        Debug method to understand the calendar data structure from Yahoo Finance.
        
        Args:
            ticker (yf.Ticker): Yahoo Finance ticker object
            symbol (str): Stock symbol for logging
            
        Note:
            This method is for debugging purposes to understand calendar data format.
        """
        try:
            calendar_data = ticker.calendar
            logger.info(f"🔍 Calendar data structure for {symbol}:")
            logger.info(f"Type: {type(calendar_data)}")
            
            if calendar_data is not None:
                if isinstance(calendar_data, dict):
                    logger.info("📋 Calendar data contents (dictionary):")
                    for key, value in calendar_data.items():
                        logger.info(f"  {key}: {value} (type: {type(value)})")
                else:
                    # Handle DataFrame case
                    if hasattr(calendar_data, 'shape'):
                        logger.info(f"Shape: {calendar_data.shape}")
                    if hasattr(calendar_data, 'columns'):
                        logger.info(f"Columns: {calendar_data.columns.tolist()}")
                    if hasattr(calendar_data, 'index'):
                        logger.info(f"Index: {calendar_data.index.tolist()}")
                    logger.info("Sample data:")
                    logger.info(calendar_data.head() if hasattr(calendar_data, 'head') else calendar_data)
            else:
                logger.info("Calendar data is None")
        except Exception as e:
            logger.error(f"Error debugging calendar for {symbol}: {e}")

    def get_earnings_data(self, ticker: yf.Ticker, symbol: str) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch earnings data from Yahoo Finance calendar including estimates and ranges.
        
        Args:
            ticker (yf.Ticker): Yahoo Finance ticker object
            symbol (str): Stock symbol for logging and data association
        
        Returns:
            Optional[List[Dict[str, Any]]]: List of earnings records, each containing:
                - Symbol: Stock symbol
                - earnings_date: Date of earnings announcement
                - earnings_high: High earnings estimate
                - earnings_low: Low earnings estimate
                - earnings_average: Average earnings estimate
                - revenue_high: High revenue estimate
                - revenue_low: Low revenue estimate
                - revenue_average: Average revenue estimate
        
        Note:
            Returns None if no calendar data is available or if an error occurs.
            Missing values are replaced with 0.0 to maintain data consistency.
        """
        try:
            # Use calendar instead of earnings_dates
            calendar_data = ticker.calendar

            if calendar_data is not None:
                earnings_data_list = []

                # Calendar data is a dictionary, not a DataFrame
                if isinstance(calendar_data, dict):
                    logger.info(f"📅 Processing calendar dictionary for {symbol}")
                    
                    # Get earnings dates - can be a single date or list of dates
                    earnings_dates = calendar_data.get('Earnings Date', [])
                    
                    # Ensure earnings_dates is a list
                    if not isinstance(earnings_dates, list):
                        earnings_dates = [earnings_dates] if earnings_dates else []
                    
                    # Process each earnings date
                    for earnings_date in earnings_dates:
                        if earnings_date:  # Check if date is not None/empty
                            try:
                                # Convert to datetime if it's a date object
                                if hasattr(earnings_date, 'year'):
                                    earnings_datetime = pd.to_datetime(earnings_date)
                                else:
                                    earnings_datetime = pd.to_datetime(earnings_date)
                                
                                earnings_data_list.append({
                                    "Symbol": symbol,
                                    'earnings_date': earnings_datetime,
                                    'earnings_high': DataUtils.safe_get_value(calendar_data, 'Earnings High', 0.0, convert_to_float=True),
                                    'earnings_low': DataUtils.safe_get_value(calendar_data, 'Earnings Low', 0.0, convert_to_float=True),
                                    'earnings_average': DataUtils.safe_get_value(calendar_data, 'Earnings Average', 0.0, convert_to_float=True),
                                    'revenue_high': DataUtils.safe_get_value(calendar_data, 'Revenue High', 0.0, convert_to_float=True),
                                    'revenue_low': DataUtils.safe_get_value(calendar_data, 'Revenue Low', 0.0, convert_to_float=True),
                                    'revenue_average': DataUtils.safe_get_value(calendar_data, 'Revenue Average', 0.0, convert_to_float=True)
                                })
                                
                                logger.info(f"✅ Added earnings record for {symbol} on {earnings_date}")
                                
                            except Exception as date_error:
                                logger.warning(f"Skipping earnings date {earnings_date} for {symbol}: {date_error}")
                                continue
                    
                    if not earnings_data_list:
                        logger.warning(f"No valid earnings dates found for {symbol}")
                        return None
                        
                else:
                    # Handle case where calendar_data might be DataFrame (legacy support)
                    logger.info(f"🔍 Calendar data is not dict for {symbol}, type: {type(calendar_data)}")
                    if hasattr(calendar_data, 'empty') and not calendar_data.empty:
                        # Legacy DataFrame handling code here if needed
                        logger.warning(f"DataFrame calendar format not implemented for {symbol}")
                        return None

                logger.info(f"✅ Fetched {len(earnings_data_list)} calendar records for {symbol}")
                return earnings_data_list
            else:
                logger.warning(f"No calendar data available for {symbol}")
                return None
        except Exception as e:
            # Re-raise rate limit errors to trigger retry logic
            if DataUtils.is_rate_limited_error(str(e)):
                logger.warning(f"🚦 Rate limit hit for {symbol} in earnings data: {e}")
                raise e
            
            logger.error(f"Error fetching calendar data for {symbol}: {e}")
            # Enable debugging to see calendar structure
            self.debug_calendar_structure(ticker, symbol)
            return None

    def get_company_info(self, ticker: yf.Ticker, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Fetch company information from Yahoo Finance info data.
        
        Args:
            ticker (yf.Ticker): Yahoo Finance ticker object
            symbol (str): Stock symbol for logging and data association
        
        Returns:
            Optional[Dict[str, Any]]: Dictionary containing company information:
                - Symbol: Stock symbol
                - company_name: Company name
                - sector: Business sector
                - industry: Specific industry
                - website: Company website
                - business_summary: Long business description
                - full_time_employees: Number of employees
                - city: Company headquarters city
                - state: Company headquarters state
                - country: Company headquarters country
                - phone: Company phone number
                - market_cap: Market capitalization
                - enterprise_value: Enterprise value
                - trailing_pe: Trailing P/E ratio
                - forward_pe: Forward P/E ratio
                - price_to_book: Price to book ratio
                - revenue_ttm: Trailing twelve months revenue
                - gross_margins: Gross profit margins
                - profit_margins: Net profit margins
        
        Note:
            Returns None if no company info is available or if an error occurs.
            Missing values are replaced with appropriate defaults.
        """
        try:
            info_data = ticker.info
            
            if info_data is not None and isinstance(info_data, dict):
                logger.info(f"📋 Processing company info for {symbol}")
                
                # Get business summary with length limit
                business_summary = DataUtils.safe_get_value(info_data, 'longBusinessSummary', '')
                if business_summary:
                    business_summary = business_summary[:Constants.BUSINESS_SUMMARY_MAX_LENGTH]
                
                company_info = {
                    "Symbol": symbol,
                    'company_name': DataUtils.safe_get_value(info_data, 'longName', symbol),
                    'sector': DataUtils.safe_get_value(info_data, 'sector', 'Unknown'),
                    'industry': DataUtils.safe_get_value(info_data, 'industry', 'Unknown'),
                    'website': DataUtils.safe_get_value(info_data, 'website', ''),
                    'business_summary': business_summary,
                    'full_time_employees': DataUtils.safe_get_value(info_data, 'fullTimeEmployees', 0.0, convert_to_float=True),
                    'city': DataUtils.safe_get_value(info_data, 'city', ''),
                    'state': DataUtils.safe_get_value(info_data, 'state', ''),
                    'country': DataUtils.safe_get_value(info_data, 'country', ''),
                    'phone': DataUtils.safe_get_value(info_data, 'phone', ''),
                    'market_cap': DataUtils.safe_get_value(info_data, 'marketCap', 0.0, convert_to_float=True),
                    'enterprise_value': DataUtils.safe_get_value(info_data, 'enterpriseValue', 0.0, convert_to_float=True),
                    'trailing_pe': DataUtils.safe_get_value(info_data, 'trailingPE', 0.0, convert_to_float=True),
                    'forward_pe': DataUtils.safe_get_value(info_data, 'forwardPE', 0.0, convert_to_float=True),
                    'price_to_book': DataUtils.safe_get_value(info_data, 'priceToBook', 0.0, convert_to_float=True),
                    'revenue_ttm': DataUtils.safe_get_value(info_data, 'totalRevenue', 0.0, convert_to_float=True),
                    'gross_margins': DataUtils.safe_get_value(info_data, 'grossMargins', 0.0, convert_to_float=True),
                    'profit_margins': DataUtils.safe_get_value(info_data, 'profitMargins', 0.0, convert_to_float=True)
                }
                
                logger.info(f"✅ Fetched company info for {symbol}: {company_info['company_name']}")
                return company_info
                
            else:
                logger.warning(f"No company info available for {symbol}")
                return None
                
        except Exception as e:
            # Re-raise rate limit errors to trigger retry logic
            if DataUtils.is_rate_limited_error(str(e)):
                logger.warning(f"🚦 Rate limit hit for {symbol} in company info: {e}")
                raise e
            
            logger.error(f"Error fetching company info for {symbol}: {e}")
            return None

    def get_sustainability_data(self, ticker: yf.Ticker, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Fetch ESG (Environmental, Social, Governance) sustainability data from Yahoo Finance.
        
        Args:
            ticker (yf.Ticker): Yahoo Finance ticker object
            symbol (str): Stock symbol for logging and data association
        
        Returns:
            Optional[Dict[str, Any]]: Dictionary containing sustainability metrics:
                - Symbol: Stock symbol
                - total_esg: Total ESG score
                - environment_score: Environmental score
                - social_score: Social score
                - governance_score: Governance score
                - esg_performance: ESG performance rating
                - rating_year: Year of the rating
                - rating_month: Month of the rating
                - highest_controversy: Highest controversy level
                - peer_count: Number of peer companies
                - peer_group: Peer group classification
                - peer_esg_min: Minimum ESG score in peer group
                - peer_esg_avg: Average ESG score in peer group
                - peer_esg_max: Maximum ESG score in peer group
        
        Note:
            Returns None if no sustainability data is available or if an error occurs.
            Missing values are replaced with appropriate defaults.
        """
        try:
            sustainability_data = ticker.sustainability
            
            if sustainability_data is not None:
                logger.info(f"🌱 Processing sustainability data for {symbol}")
                
                # Extract ESG scores data (assuming it's nested in esgScores)
                esg_scores = sustainability_data if isinstance(sustainability_data, dict) else {}
                if 'esgScores' in esg_scores:
                    esg_scores = esg_scores['esgScores']
                
                sustainability_info = {
                    "Symbol": symbol,
                    'total_esg': DataUtils.safe_get_nested_value(esg_scores, ['totalEsg'], 0.0, convert_to_float=True),
                    'environment_score': DataUtils.safe_get_nested_value(esg_scores, ['environmentScore'], 0.0, convert_to_float=True),
                    'social_score': DataUtils.safe_get_nested_value(esg_scores, ['socialScore'], 0.0, convert_to_float=True),
                    'governance_score': DataUtils.safe_get_nested_value(esg_scores, ['governanceScore'], 0.0, convert_to_float=True),
                    'esg_performance': DataUtils.safe_get_nested_value(esg_scores, ['esgPerformance'], 'Unknown'),
                    'rating_year': DataUtils.safe_get_nested_value(esg_scores, ['ratingYear'], 0.0, convert_to_float=True),
                    'rating_month': DataUtils.safe_get_nested_value(esg_scores, ['ratingMonth'], 0.0, convert_to_float=True),
                    'highest_controversy': DataUtils.safe_get_nested_value(esg_scores, ['highestControversy'], 0.0, convert_to_float=True),
                    'peer_count': DataUtils.safe_get_nested_value(esg_scores, ['peerCount'], 0.0, convert_to_float=True),
                    'peer_group': DataUtils.safe_get_nested_value(esg_scores, ['peerGroup'], 'Unknown'),
                    'peer_esg_min': DataUtils.safe_get_nested_value(esg_scores, ['peerEsgScorePerformance', 'min'], 0.0, convert_to_float=True),
                    'peer_esg_avg': DataUtils.safe_get_nested_value(esg_scores, ['peerEsgScorePerformance', 'avg'], 0.0, convert_to_float=True),
                    'peer_esg_max': DataUtils.safe_get_nested_value(esg_scores, ['peerEsgScorePerformance', 'max'], 0.0, convert_to_float=True)
                }
                
                logger.info(f"✅ Fetched sustainability data for {symbol}: ESG Score {sustainability_info['total_esg']}")
                return sustainability_info
                
            else:
                logger.warning(f"No sustainability data available for {symbol}")
                return None
                
        except Exception as e:
            # Re-raise rate limit errors to trigger retry logic
            if DataUtils.is_rate_limited_error(str(e)):
                logger.warning(f"🚦 Rate limit hit for {symbol} in sustainability data: {e}")
                raise e
            
            logger.error(f"Error fetching sustainability data for {symbol}: {e}")
            return None

    def get_quarterly_financials_data(self, ticker: yf.Ticker, symbol: str) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch detailed quarterly financial statements from Yahoo Finance.
        
        Args:
            ticker (yf.Ticker): Yahoo Finance ticker object
            symbol (str): Stock symbol for logging and data association
        
        Returns:
            Optional[List[Dict[str, Any]]]: List of quarterly financial records, each containing:
                - Symbol: Stock symbol
                - quarter_date: Quarter end date
                - total_revenue: Total revenue for the quarter
                - cost_of_revenue: Cost of revenue
                - gross_profit: Gross profit
                - operating_income: Operating income
                - ebitda: Earnings before interest, taxes, depreciation, and amortization
                - ebit: Earnings before interest and taxes
                - net_income: Net income
                - basic_eps: Basic earnings per share
                - diluted_eps: Diluted earnings per share
                - operating_cash_flow: Operating cash flow
                - free_cash_flow: Free cash flow
                - total_debt: Total debt
                - total_cash: Total cash and cash equivalents
        
        Note:
            Returns None if no quarterly financials are available or if an error occurs.
            Missing values are replaced with 0.0 to maintain data consistency.
        """
        try:
            quarterly_financials = ticker.quarterly_financials
            
            if quarterly_financials is not None and not quarterly_financials.empty:
                logger.info(f"📈 Processing quarterly financials for {symbol}")
                
                financials_data_list = []
                
                # Iterate through quarters (columns are quarter dates)
                for quarter_date in quarterly_financials.columns:
                    try:
                        quarter_data = quarterly_financials[quarter_date]
                        
                        quarterly_record = {
                            "Symbol": symbol,
                            'quarter_date': quarter_date.date() if hasattr(quarter_date, 'date') else quarter_date,
                            'total_revenue': DataUtils.safe_get_financial_metric(quarter_data, ['Total Revenue']),
                            'cost_of_revenue': DataUtils.safe_get_financial_metric(quarter_data, ['Cost Of Revenue', 'Reconciled Cost Of Revenue']),
                            'gross_profit': DataUtils.safe_get_financial_metric(quarter_data, ['Gross Profit']),
                            'operating_income': DataUtils.safe_get_financial_metric(quarter_data, ['Operating Income']),
                            'ebitda': DataUtils.safe_get_financial_metric(quarter_data, ['EBITDA', 'Normalized EBITDA']),
                            'ebit': DataUtils.safe_get_financial_metric(quarter_data, ['EBIT']),
                            'net_income': DataUtils.safe_get_financial_metric(quarter_data, ['Net Income', 'Net Income From Continuing Operation Net Minority Interest']),
                            'basic_eps': DataUtils.safe_get_financial_metric(quarter_data, ['Basic EPS']),
                            'diluted_eps': DataUtils.safe_get_financial_metric(quarter_data, ['Diluted EPS']),
                            'operating_cash_flow': DataUtils.safe_get_financial_metric(quarter_data, ['Operating Cash Flow']),
                            'free_cash_flow': DataUtils.safe_get_financial_metric(quarter_data, ['Free Cash Flow']),
                            'total_debt': DataUtils.safe_get_financial_metric(quarter_data, ['Total Debt']),
                            'total_cash': DataUtils.safe_get_financial_metric(quarter_data, ['Cash And Cash Equivalents', 'Total Cash'])
                        }
                        
                        financials_data_list.append(quarterly_record)
                        
                    except Exception as quarter_error:
                        logger.warning(f"Skipping quarter {quarter_date} for {symbol}: {quarter_error}")
                        continue
                
                logger.info(f"✅ Fetched {len(financials_data_list)} quarterly financial records for {symbol}")
                return financials_data_list if financials_data_list else None
                
            else:
                logger.warning(f"No quarterly financials available for {symbol}")
                return None
                
        except Exception as e:
            # Re-raise rate limit errors to trigger retry logic
            if DataUtils.is_rate_limited_error(str(e)):
                logger.warning(f"🚦 Rate limit hit for {symbol} in quarterly financials: {e}")
                raise e
            
            logger.error(f"Error fetching quarterly financials for {symbol}: {e}")
            return None

    def get_quarterly_financials(self, ticker: yf.Ticker, symbol: str) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch quarterly financial data, specifically total revenue, from Yahoo Finance.
        
        Args:
            ticker (yf.Ticker): Yahoo Finance ticker object
            symbol (str): Stock symbol for logging and data association
        
        Returns:
            Optional[List[Dict[str, Any]]]: List of quarterly financial records, each containing:
                - Symbol: Stock symbol
                - quarter_end_date: End date of the quarter (renamed to quarter_date in processing)
                - total_revenue: Total revenue for the quarter
        
        Note:
            Returns None if no quarterly financial data is available or if an error occurs.
            Missing revenue values are replaced with 0.0, and quarters with missing
            data are skipped with appropriate logging.
        """
        try:
            quarterly_financials = ticker.quarterly_income_stmt

            if quarterly_financials is not None and not quarterly_financials.empty:
                quarter_end_dates = quarterly_financials.columns
                financial_data_list = []

                for quarter_end in quarter_end_dates:
                    try:
                        total_revenue = quarterly_financials.loc['Total Revenue', quarter_end]
                        if pd.isna(total_revenue):
                            total_revenue = 0.0
                        
                        financial_data_list.append({
                            'Symbol': symbol,
                            'quarter_end_date': quarter_end.date(),
                            'total_revenue': float(total_revenue)
                        })
                    except (KeyError, ValueError) as e:
                        logger.warning(f"Skipping quarter {quarter_end} for {symbol}: {e}")
                        continue

                logger.info(f"✅ Fetched {len(financial_data_list)} quarterly records for {symbol}")
                return financial_data_list
            else:
                logger.warning(f"No quarterly financials available for {symbol}")
                return None
        except Exception as e:
            # Re-raise rate limit errors to trigger retry logic
            if DataUtils.is_rate_limited_error(str(e)):
                logger.warning(f"🚦 Rate limit hit for {symbol} in quarterly financials: {e}")
                raise e
            
            logger.error(f"Error fetching quarterly financials for {symbol}: {e}")
            return None

    def get_stock_symbols(self) -> List[str]:
        """
        Retrieve stock symbols to process from configuration or database.
        
        This method first attempts to read symbols from the configuration file.
        If no symbols are found in the config, it queries the database to get
        all active stock symbols from the stock tracker table.
        
        Returns:
            List[str]: List of stock symbols to process
            
        Note:
            If both configuration and database queries fail, returns an empty list.
            The method logs the source and count of symbols found for debugging.
        """
        # Get symbols from config, handling empty/whitespace-only values
        ticker_list = self.config.get('stocks', 'symbols', fallback='').split() if self.config.get('stocks', 'symbols', fallback='').strip() else []
        
        if not ticker_list:
            logger.info("🔄 Config ticker list is empty, pulling from database...")
            try:
                # Load active ticker symbols from change tracker table
                change_tracker_df = self.spark.read.format("jdbc").options(
                    url=self.url,
                    driver=self.sql_driver,
                    user=self.username,
                    password=self.password,
                    dbtable=self.stock_tracker_table
                ).load()

                # Filter for only active stocks and get their symbols
                change_tracker_df = change_tracker_df.filter(col("is_active") == True).select("symbol")
                ticker_list = change_tracker_df.rdd.flatMap(lambda x: x).collect()
                
                logger.info(f"📋 Found {len(ticker_list)} active stocks from database")
                if ticker_list:
                    # Show the tracker data for debugging purposes
                    logger.info("📋 Active stocks from database:")
                    change_tracker_df.show(10, truncate=False)
                    
            except Exception as e:
                logger.error(f"Error loading symbols from database: {e}")
                return []
        else:
            logger.info(f"📋 Using symbols from config: {ticker_list}")
        
        logger.info(f"Processing {len(ticker_list)} symbols: {ticker_list}")
        return ticker_list

    def filter_symbols_with_future_earnings(self, symbols: List[str]) -> List[str]:
        """
        Filter out symbols that already have future earnings dates in the database.
        
        This optimization prevents unnecessary API calls for symbols that already
        have upcoming earnings data stored.
        
        Args:
            symbols (List[str]): List of all symbols to check
            
        Returns:
            List[str]: Filtered list of symbols that need earnings data updates
            
        Note:
            Symbols with earnings dates in the future (> today) are filtered out
            to avoid redundant API calls and processing.
        """
        if not symbols:
            return symbols
            
        try:
            logger.info("🔍 Checking for symbols with existing future earnings dates...")
            
            # Load existing earnings data
            existing_earnings_df = self.spark.read.format("jdbc").options(
                url=self.url,
                driver=self.sql_driver,
                user=self.username,
                password=self.password,
                dbtable=self.earnings_table
            ).load()
            
            if existing_earnings_df.count() == 0:
                logger.info("📋 No existing earnings data found - processing all symbols")
                return symbols
            
            # Get current date
            from datetime import date
            today = date.today()
            
            # Convert earnings_date to date for comparison
            existing_earnings_df = existing_earnings_df.withColumn(
                "earnings_date", to_date(col("earnings_date"))
            )
            
            # Find symbols with future earnings dates
            future_earnings_df = existing_earnings_df.filter(
                col("earnings_date") > lit(today.strftime('%Y-%m-%d'))
            ).select("Symbol").distinct()
            
            # Get list of symbols with future earnings
            symbols_with_future_earnings = [row.Symbol for row in future_earnings_df.collect()]
            
            # Filter out symbols that already have future earnings
            symbols_to_process = [symbol for symbol in symbols 
                                if symbol not in symbols_with_future_earnings]
            
            # Log the filtering results
            if symbols_with_future_earnings:
                logger.info(f"⏭️ Skipping {len(symbols_with_future_earnings)} symbols with future earnings: {symbols_with_future_earnings}")
            
            if symbols_to_process:
                logger.info(f"🎯 Processing {len(symbols_to_process)} symbols that need updates: {symbols_to_process}")
            else:
                logger.info("✅ All symbols already have future earnings dates - nothing to process!")
            
            return symbols_to_process
            
        except Exception as e:
            logger.warning(f"⚠️ Error filtering symbols with future earnings: {e}")
            logger.info("📋 Proceeding with all symbols due to filtering error")
            return symbols

    def smart_filter_symbols_for_updates(self, symbols: List[str], days_threshold: int = 7) -> List[str]:
        """
        Smart filtering that optimizes API calls by skipping symbols with future earnings data.
        
        This method balances API call optimization with data freshness by:
        1. Skipping symbols that have ANY future earnings dates (API call optimization)
        2. Only processing symbols that have no earnings data or past earnings dates
        3. Optionally allowing updates for very old earnings data (based on threshold)
        
        Args:
            symbols (List[str]): List of all symbols to check
            days_threshold (int): Allow updates for earnings data older than this many days
            
        Returns:
            List[str]: Filtered list of symbols that need earnings data updates
        """
        if not symbols:
            return symbols
            
        try:
            logger.info(f"🔍 Smart filtering: optimizing API calls by skipping symbols with future earnings...")
            
            # Load existing earnings data
            existing_earnings_df = self.spark.read.format("jdbc").options(
                url=self.url,
                driver=self.sql_driver,
                user=self.username,
                password=self.password,
                dbtable=self.earnings_table
            ).load()
            
            if existing_earnings_df.count() == 0:
                logger.info("📋 No existing earnings data found - processing all symbols")
                return symbols
            
            # Get current date
            from datetime import date, timedelta
            today = date.today()
            
            # Convert earnings_date to date for comparison
            existing_earnings_df = existing_earnings_df.withColumn(
                "earnings_date", to_date(col("earnings_date"))
            )
            
            # Find symbols that have ANY future earnings dates (skip these for API optimization)
            symbols_with_future_earnings_df = existing_earnings_df.filter(
                col("earnings_date") > lit(today.strftime('%Y-%m-%d'))
            ).select("Symbol").distinct()
            
            # Get list of symbols with future earnings (skip these)
            symbols_to_skip = [row.Symbol for row in symbols_with_future_earnings_df.collect()]
            
            # Optional: Allow updates for very old earnings data (if needed)
            # This allows refreshing earnings data that might be stale
            if days_threshold > 0:
                old_threshold = today - timedelta(days=days_threshold)
                
                # Find symbols with very old earnings data that might need refreshing
                very_old_earnings_df = existing_earnings_df.filter(
                    col("earnings_date") < lit(old_threshold.strftime('%Y-%m-%d'))
                ).select("Symbol").distinct()
                
                symbols_needing_refresh = [row.Symbol for row in very_old_earnings_df.collect()]
                
                # Remove symbols that need refresh from the skip list
                symbols_to_skip = [symbol for symbol in symbols_to_skip 
                                 if symbol not in symbols_needing_refresh]
                
                if symbols_needing_refresh:
                    logger.info(f"🔄 Allowing refresh for {len(symbols_needing_refresh)} symbols with old earnings data: {symbols_needing_refresh}")
            
            # Filter out symbols that can be skipped for API optimization
            symbols_to_process = [symbol for symbol in symbols 
                                if symbol not in symbols_to_skip]
            
            # Log the filtering results
            if symbols_to_skip:
                logger.info(f"⚡ API Optimization: Skipping {len(symbols_to_skip)} symbols with future earnings dates")
                logger.info(f"   Symbols skipped: {symbols_to_skip}")
            
            if symbols_to_process:
                logger.info(f"🎯 Processing {len(symbols_to_process)} symbols that need updates")
                logger.info(f"   Will process: {symbols_to_process}")
            else:
                logger.info("✅ All symbols have future earnings data - API calls optimized!")
            
            return symbols_to_process
            
        except Exception as e:
            logger.warning(f"⚠️ Error in smart filtering: {e}")
            logger.info("📋 Proceeding with all symbols due to filtering error")
            return symbols

    def process_single_ticker(self, symbol: str) -> Dict[str, Any]:
        """
        Process a single ticker symbol and collect all its data with retry logic for rate limits.
        
        This method is designed to be thread-safe and will be called by multiple threads.
        Includes intelligent retry mechanism for rate limiting errors.
        
        Args:
            symbol (str): Stock symbol to process
            
        Returns:
            Dict[str, Any]: Dictionary containing all data types for the symbol:
                - earnings_data: List of earnings records
                - quarterly_data: List of quarterly financial records  
                - company_info: Company information dictionary
                - sustainability_data: Sustainability ESG dictionary
                - quarterly_financials_data: List of quarterly financials records
                - success: Boolean indicating if processing was successful
                - error: Error message if processing failed
        """
        thread_id = threading.current_thread().name
        
        # Check if we need to wait due to recent rate limits from other threads
        self._check_global_rate_limit(thread_id)
        
        for attempt in range(self.max_retries + 1):  # 0, 1, 2, 3 (4 total attempts)
            try:
                if attempt > 0:
                    logger.info(f"🔄 [{thread_id}] Retry attempt {attempt}/{self.max_retries} for {symbol}...")
                else:
                    logger.info(f"🧵 [{thread_id}] Processing {symbol}...")
                
                result = {
                    'symbol': symbol,
                    'earnings_data': [],
                    'quarterly_data': [],
                    'company_info': None,
                    'sustainability_data': None,
                    'quarterly_financials_data': [],
                    'success': False,
                    'error': None
                }
                
                ticker = self.get_ticker(symbol)
                if not ticker:
                    result['error'] = "Could not fetch ticker"
                    logger.warning(f"⚠️ [{thread_id}] Skipping {symbol} - could not fetch ticker")
                    return result
                
                # Fetch earnings data
                try:
                    earnings_data = self.get_earnings_data(ticker, symbol)
                    if earnings_data:
                        result['earnings_data'] = earnings_data
                        logger.info(f"✅ [{thread_id}] {symbol}: Fetched {len(earnings_data)} earnings records")
                except Exception as e:
                    if DataUtils.is_rate_limited_error(str(e)):
                        self._record_rate_limit_hit(thread_id)  # Record rate limit hit
                        raise e  # Re-raise to trigger outer retry loop
                    logger.warning(f"⚠️ [{thread_id}] {symbol}: Error fetching earnings data: {e}")
                
                # Fetch quarterly data
                try:
                    quarterly_data = self.get_quarterly_financials(ticker, symbol)
                    if quarterly_data:
                        result['quarterly_data'] = quarterly_data
                        logger.info(f"✅ [{thread_id}] {symbol}: Fetched {len(quarterly_data)} quarterly records")
                except Exception as e:
                    if DataUtils.is_rate_limited_error(str(e)):
                        self._record_rate_limit_hit(thread_id)  # Record rate limit hit
                        raise e  # Re-raise to trigger outer retry loop
                    logger.warning(f"⚠️ [{thread_id}] {symbol}: Error fetching quarterly data: {e}")
                
                # Fetch company info data
                try:
                    company_info = self.get_company_info(ticker, symbol)
                    if company_info:
                        result['company_info'] = company_info
                        logger.info(f"✅ [{thread_id}] {symbol}: Fetched company info")
                except Exception as e:
                    if DataUtils.is_rate_limited_error(str(e)):
                        self._record_rate_limit_hit(thread_id)  # Record rate limit hit
                        raise e  # Re-raise to trigger outer retry loop
                    logger.warning(f"⚠️ [{thread_id}] {symbol}: Error fetching company info: {e}")
                
                # Fetch sustainability data
                try:
                    sustainability_data = self.get_sustainability_data(ticker, symbol)
                    if sustainability_data:
                        result['sustainability_data'] = sustainability_data
                        logger.info(f"✅ [{thread_id}] {symbol}: Fetched sustainability data")
                except Exception as e:
                    if DataUtils.is_rate_limited_error(str(e)):
                        self._record_rate_limit_hit(thread_id)  # Record rate limit hit
                        raise e  # Re-raise to trigger outer retry loop
                    logger.warning(f"⚠️ [{thread_id}] {symbol}: Error fetching sustainability data: {e}")
                
                # Fetch quarterly financials data
                try:
                    quarterly_financials_data = self.get_quarterly_financials_data(ticker, symbol)
                    if quarterly_financials_data:
                        result['quarterly_financials_data'] = quarterly_financials_data
                        logger.info(f"✅ [{thread_id}] {symbol}: Fetched {len(quarterly_financials_data)} quarterly financials records")
                except Exception as e:
                    if DataUtils.is_rate_limited_error(str(e)):
                        self._record_rate_limit_hit(thread_id)  # Record rate limit hit
                        raise e  # Re-raise to trigger outer retry loop
                    logger.warning(f"⚠️ [{thread_id}] {symbol}: Error fetching quarterly financials: {e}")
                
                result['success'] = True
                logger.info(f"🎯 [{thread_id}] Completed processing {symbol}")
                return result
                
            except Exception as e:
                # Check if this is a rate limit error using centralized utility
                if DataUtils.is_rate_limited_error(str(e)):
                    self._record_rate_limit_hit(thread_id)  # Record rate limit hit
                    if attempt < self.max_retries:
                        # Progressive backoff: increase sleep time with each retry
                        progressive_sleep = self.retry_sleep_seconds * (attempt + 1)
                        logger.warning(f"🚦 [{thread_id}] Rate limit detected for {symbol}. Sleeping {progressive_sleep}s before retry {attempt + 1}/{self.max_retries}")
                        time.sleep(progressive_sleep)
                        continue  # Retry the entire symbol processing
                    else:
                        logger.error(f"❌ [{thread_id}] Max retries exceeded for {symbol} due to rate limiting: {e}")
                        return {
                            'symbol': symbol,
                            'earnings_data': [],
                            'quarterly_data': [],
                            'company_info': None,
                            'sustainability_data': None,
                            'quarterly_financials_data': [],
                            'success': False,
                            'error': f"Rate limit exceeded after {self.max_retries} retries: {str(e)}"
                        }
                else:
                    # Non-rate-limit error, don't retry
                    logger.error(f"❌ [{thread_id}] Error processing {symbol}: {e}")
                    return {
                        'symbol': symbol,
                        'earnings_data': [],
                        'quarterly_data': [],
                        'company_info': None,
                        'sustainability_data': None,
                        'quarterly_financials_data': [],
                        'success': False,
                        'error': str(e)
                    }
        
        # Should not reach here, but just in case
        return {
            'symbol': symbol,
            'earnings_data': [],
            'quarterly_data': [],
            'company_info': None,
            'sustainability_data': None,
            'quarterly_financials_data': [],
            'success': False,
            'error': "Unexpected error in retry logic"
        }
    
    def collect_ticker_results(self, results: List[Dict[str, Any]]):
        """
        Thread-safely collect results from all ticker processing.
        Only includes symbols that have actual data to prevent empty database updates.
        
        Args:
            results (List[Dict[str, Any]]): List of results from process_single_ticker
        """
        successful_symbols = []
        failed_symbols = []
        symbols_with_data = {
            'earnings': [],
            'quarterly': [],
            'company_info': [],
            'sustainability': [],
            'quarterly_financials': []
        }
        
        with self.data_lock:
            for result in results:
                if result['success']:
                    symbol = result['symbol']
                    has_any_data = False
                    
                    # Collect earnings data (only if it contains actual records)
                    if result['earnings_data'] and len(result['earnings_data']) > 0:
                        self.all_earnings_data.extend(result['earnings_data'])
                        symbols_with_data['earnings'].append(symbol)
                        has_any_data = True
                        logger.debug(f"📊 {symbol}: Added {len(result['earnings_data'])} earnings records")
                    
                    # Collect quarterly data (only if it contains actual records)
                    if result['quarterly_data'] and len(result['quarterly_data']) > 0:
                        self.all_quarterly_data.extend(result['quarterly_data'])
                        symbols_with_data['quarterly'].append(symbol)
                        has_any_data = True
                        logger.debug(f"📊 {symbol}: Added {len(result['quarterly_data'])} quarterly records")
                    
                    # Collect company info (only if it contains actual data)
                    if result['company_info'] and isinstance(result['company_info'], dict):
                        # Additional validation: ensure it's not just empty values
                        company_info = result['company_info']
                        if (company_info.get('company_name') and company_info.get('company_name') != company_info.get('Symbol', '')):
                            self.all_company_info_data.append(company_info)
                            symbols_with_data['company_info'].append(symbol)
                            has_any_data = True
                            logger.debug(f"📊 {symbol}: Added company info for {company_info.get('company_name', 'Unknown')}")
                    
                    # Collect sustainability data (only if it contains meaningful data)
                    if result['sustainability_data'] and isinstance(result['sustainability_data'], dict):
                        sustainability_data = result['sustainability_data']
                        # Check if it has actual ESG scores (not just zeros)
                        if (sustainability_data.get('total_esg', 0) > 0 or 
                            sustainability_data.get('environment_score', 0) > 0 or 
                            sustainability_data.get('social_score', 0) > 0):
                            self.all_sustainability_data.append(sustainability_data)
                            symbols_with_data['sustainability'].append(symbol)
                            has_any_data = True
                            logger.debug(f"📊 {symbol}: Added sustainability data (ESG: {sustainability_data.get('total_esg', 0)})")
                    
                    # Collect quarterly financials data (only if it contains actual records)
                    if result['quarterly_financials_data'] and len(result['quarterly_financials_data']) > 0:
                        self.all_quarterly_financials_data.extend(result['quarterly_financials_data'])
                        symbols_with_data['quarterly_financials'].append(symbol)
                        has_any_data = True
                        logger.debug(f"📊 {symbol}: Added {len(result['quarterly_financials_data'])} quarterly financials records")
                    
                    # Only count as successful if we actually got some data
                    if has_any_data:
                        successful_symbols.append(symbol)
                    else:
                        logger.warning(f"⚠️ {symbol}: Processed successfully but no usable data found - will not update database")
                        failed_symbols.append(symbol)  # Treat as failed to preserve existing data
                else:
                    failed_symbols.append(result['symbol'])
        
        # Enhanced logging with data type breakdown
        if successful_symbols:
            logger.info(f"✅ Successfully collected data for {len(successful_symbols)} symbols: {successful_symbols}")
            logger.info(f"📊 Data breakdown:")
            logger.info(f"   Earnings: {len(symbols_with_data['earnings'])} symbols, {len(self.all_earnings_data)} records")
            logger.info(f"   Quarterly: {len(symbols_with_data['quarterly'])} symbols, {len(self.all_quarterly_data)} records")
            logger.info(f"   Company Info: {len(symbols_with_data['company_info'])} symbols")
            logger.info(f"   Sustainability: {len(symbols_with_data['sustainability'])} symbols")
            logger.info(f"   Quarterly Financials: {len(symbols_with_data['quarterly_financials'])} symbols, {len(self.all_quarterly_financials_data)} records")
        
        if failed_symbols:
            logger.warning(f"⚠️ Skipped database updates for {len(failed_symbols)} symbols (existing data preserved): {failed_symbols}")
            logger.info("🔒 Data preservation: Existing database records for these symbols will remain unchanged")
        
        # Validation check: ensure we don't accidentally have empty data collections
        total_data_points = (len(self.all_earnings_data) + len(self.all_quarterly_data) + 
                           len(self.all_company_info_data) + len(self.all_sustainability_data) + 
                           len(self.all_quarterly_financials_data))
        
        if len(successful_symbols) > 0 and total_data_points == 0:
            logger.error("🚨 VALIDATION ERROR: Successful symbols reported but no data collected!")
            logger.error("🚨 This indicates a data collection bug - aborting database updates")
            raise ValueError("Data collection validation failed: no data despite successful symbols")
        
        logger.info(f"📊 Total data validation: {total_data_points} data points collected for {len(successful_symbols)} symbols")

    def process_earnings_data(self, earnings_data: List[Dict]) -> pd.DataFrame:
        """
        Process and clean raw earnings calendar data into a structured pandas DataFrame.
        
        Args:
            earnings_data (List[Dict]): Raw earnings calendar data from Yahoo Finance
        
        Returns:
            pd.DataFrame: Cleaned DataFrame with proper data types and filled NaN values
            
        Note:
            - Converts earnings_date to datetime format
            - Fills NaN values in numeric columns with 0.0
            - Returns empty DataFrame with proper columns if no data provided
        """
        earnings_columns = ['Symbol', 'earnings_date'] + Constants.EARNINGS_COLUMNS
        
        if not earnings_data:
            return pd.DataFrame(columns=earnings_columns)
        
        df = pd.DataFrame(earnings_data)
        
        # Convert earnings_date to proper datetime format
        df['earnings_date'] = pd.to_datetime(df['earnings_date'], errors='coerce')
        
        # Fill NaN values with appropriate defaults
        df[Constants.EARNINGS_COLUMNS] = df[Constants.EARNINGS_COLUMNS].fillna(0.0)
        
        return df

    def process_quarterly_data(self, quarterly_data: List[Dict]) -> pd.DataFrame:
        """
        Process and clean raw quarterly financial data into a structured pandas DataFrame.
        
        Args:
            quarterly_data (List[Dict]): Raw quarterly financial data from Yahoo Finance
        
        Returns:
            pd.DataFrame: Cleaned DataFrame with proper data types and filled NaN values
            
        Note:
            - Converts quarter_end_date to datetime format then renames to quarter_date
            - Fills NaN values in total_revenue with 0.0  
            - Returns empty DataFrame with proper columns if no data provided
        """
        if not quarterly_data:
            return pd.DataFrame(columns=['Symbol', 'quarter_end_date', 'total_revenue'])
        
        df = pd.DataFrame(quarterly_data)
        
        # Convert quarter_end_date to proper date format
        df['quarter_end_date'] = pd.to_datetime(df['quarter_end_date'], errors='coerce')
        
        # Rename to match database schema
        df = df.rename(columns={'quarter_end_date': 'quarter_date'})
        
        # Fill NaN values
        df['total_revenue'] = df['total_revenue'].fillna(0.0)
        
        return df

    def process_company_info_data(self, company_info_data: List[Dict]) -> pd.DataFrame:
        """
        Process and clean raw company info data into a structured pandas DataFrame.
        
        Args:
            company_info_data (List[Dict]): Raw company info data from Yahoo Finance
        
        Returns:
            pd.DataFrame: Cleaned DataFrame with proper data types and filled values
            
        Note:
            - Fills NaN values in numeric columns with 0.0
            - Fills NaN values in string columns with empty string
            - Returns empty DataFrame with proper columns if no data provided
        """
        company_info_columns = Constants.COMPANY_INFO_STRING_COLUMNS + Constants.COMPANY_INFO_NUMERIC_COLUMNS
        
        if not company_info_data:
            return pd.DataFrame(columns=company_info_columns)
        
        df = pd.DataFrame(company_info_data)
        
        # Fill NaN values with appropriate defaults
        df[Constants.COMPANY_INFO_STRING_COLUMNS] = df[Constants.COMPANY_INFO_STRING_COLUMNS].fillna('')
        df[Constants.COMPANY_INFO_NUMERIC_COLUMNS] = df[Constants.COMPANY_INFO_NUMERIC_COLUMNS].fillna(0.0)
        
        return df

    def process_sustainability_data(self, sustainability_data: List[Dict]) -> pd.DataFrame:
        """
        Process and clean raw sustainability ESG data into a structured pandas DataFrame.
        
        Args:
            sustainability_data (List[Dict]): Raw sustainability data from Yahoo Finance
        
        Returns:
            pd.DataFrame: Cleaned DataFrame with proper data types and filled values
            
        Note:
            - Fills NaN values in numeric columns with 0.0
            - Fills NaN values in string columns with appropriate defaults
            - Returns empty DataFrame with proper columns if no data provided
        """
        sustainability_columns = Constants.SUSTAINABILITY_STRING_COLUMNS + Constants.SUSTAINABILITY_NUMERIC_COLUMNS
        
        if not sustainability_data:
            return pd.DataFrame(columns=sustainability_columns)
        
        df = pd.DataFrame(sustainability_data)
        
        # Fill NaN values with appropriate defaults
        df[Constants.SUSTAINABILITY_STRING_COLUMNS] = df[Constants.SUSTAINABILITY_STRING_COLUMNS].fillna('')
        df[Constants.SUSTAINABILITY_NUMERIC_COLUMNS] = df[Constants.SUSTAINABILITY_NUMERIC_COLUMNS].fillna(0.0)
        
        return df

    def process_quarterly_financials_data(self, quarterly_financials_data: List[Dict]) -> pd.DataFrame:
        """
        Process and clean raw quarterly financials data into a structured pandas DataFrame.
        
        Args:
            quarterly_financials_data (List[Dict]): Raw quarterly financials data from Yahoo Finance
        
        Returns:
            pd.DataFrame: Cleaned DataFrame with proper data types and filled values
            
        Note:
            - Converts quarter_date to datetime format
            - Fills NaN values in numeric columns with 0.0
            - Returns empty DataFrame with proper columns if no data provided
        """
        quarterly_financials_columns = ['Symbol', 'quarter_date'] + Constants.QUARTERLY_FINANCIALS_COLUMNS
        
        if not quarterly_financials_data:
            return pd.DataFrame(columns=quarterly_financials_columns)
        
        df = pd.DataFrame(quarterly_financials_data)
        
        # Convert quarter_date to proper date format
        df['quarter_date'] = pd.to_datetime(df['quarter_date'], errors='coerce')
        
        # Fill NaN values with appropriate defaults
        df[Constants.QUARTERLY_FINANCIALS_COLUMNS] = df[Constants.QUARTERLY_FINANCIALS_COLUMNS].fillna(0.0)
        
        return df

    def create_spark_dataframes(self, earnings_df: pd.DataFrame, quarterly_df: pd.DataFrame, 
                               company_info_df: pd.DataFrame = None, sustainability_df: pd.DataFrame = None,
                               quarterly_financials_df: pd.DataFrame = None):
        """
        Convert pandas DataFrames to Spark DataFrames with proper schemas and data types.
        
        Args:
            earnings_df (pd.DataFrame): Processed earnings data
            quarterly_df (pd.DataFrame): Processed quarterly financial data
            company_info_df (pd.DataFrame, optional): Processed company info data
            sustainability_df (pd.DataFrame, optional): Processed sustainability data
            quarterly_financials_df (pd.DataFrame, optional): Processed quarterly financials data
        
        Returns:
            Tuple[DataFrame, DataFrame, DataFrame, DataFrame, DataFrame]: Tuple containing:
                - earnings_spark_df: Spark DataFrame with earnings data
                - quarterly_spark_df: Spark DataFrame with quarterly data
                - company_info_spark_df: Spark DataFrame with company info data
                - sustainability_spark_df: Spark DataFrame with sustainability data
                - quarterly_financials_spark_df: Spark DataFrame with quarterly financials data
                
        Note:
            - Defines proper schemas with appropriate data types
            - Handles datetime conversion from pandas to Spark format
            - Creates empty DataFrames with correct schemas if input is empty
        """
        
        # Convert to Spark DataFrames using helper schemas
        if not earnings_df.empty:
            # Convert datetime to timestamp strings for Spark
            earnings_df_copy = earnings_df.copy()
            earnings_df_copy['earnings_date'] = earnings_df_copy['earnings_date'].apply(
                lambda x: x.strftime('%Y-%m-%d %H:%M:%S') if pd.notnull(x) else None
            )
            
            earnings_spark_df = self.spark.createDataFrame(
                earnings_df_copy.to_dict(orient='records'), 
                SchemaHelper.get_earnings_schema()
            )
            # Convert string to timestamp after DataFrame creation
            earnings_spark_df = earnings_spark_df.withColumn(
                "earnings_date", 
                to_timestamp(col("earnings_date"), "yyyy-MM-dd HH:mm:ss")
            )
        else:
            earnings_spark_df = self.spark.createDataFrame([], SchemaHelper.get_earnings_schema())
            earnings_spark_df = earnings_spark_df.withColumn("earnings_date", to_timestamp(col("earnings_date")))
        
        if not quarterly_df.empty:
            quarterly_df_copy = quarterly_df.copy()
            quarterly_df_copy['quarter_date'] = quarterly_df_copy['quarter_date'].apply(
                lambda x: x.strftime('%Y-%m-%d') if pd.notnull(x) else None
            )
            
            quarterly_spark_df = self.spark.createDataFrame(
                quarterly_df_copy.to_dict(orient='records'), 
                SchemaHelper.get_quarterly_schema()
            )
            # Convert string to date after DataFrame creation
            quarterly_spark_df = quarterly_spark_df.withColumn(
                "quarter_date", 
                to_date(col("quarter_date"), "yyyy-MM-dd")
            )
        else:
            quarterly_spark_df = self.spark.createDataFrame([], SchemaHelper.get_quarterly_schema())
            quarterly_spark_df = quarterly_spark_df.withColumn("quarter_date", to_date(col("quarter_date")))
        
        # Convert company info DataFrame
        if company_info_df is not None and not company_info_df.empty:
            company_info_spark_df = self.spark.createDataFrame(
                company_info_df.to_dict(orient='records'), 
                SchemaHelper.get_company_info_schema()
            )
        else:
            company_info_spark_df = self.spark.createDataFrame([], SchemaHelper.get_company_info_schema())
        
        # Convert sustainability DataFrame
        if sustainability_df is not None and not sustainability_df.empty:
            sustainability_spark_df = self.spark.createDataFrame(
                sustainability_df.to_dict(orient='records'), 
                SchemaHelper.get_sustainability_schema()
            )
        else:
            sustainability_spark_df = self.spark.createDataFrame([], SchemaHelper.get_sustainability_schema())
        
        # Convert quarterly financials DataFrame
        if quarterly_financials_df is not None and not quarterly_financials_df.empty:
            quarterly_financials_df_copy = quarterly_financials_df.copy()
            quarterly_financials_df_copy['quarter_date'] = quarterly_financials_df_copy['quarter_date'].apply(
                lambda x: x.strftime('%Y-%m-%d') if pd.notnull(x) else None
            )
            
            quarterly_financials_spark_df = self.spark.createDataFrame(
                quarterly_financials_df_copy.to_dict(orient='records'), 
                SchemaHelper.get_quarterly_financials_schema()
            )
            # Convert string to date after DataFrame creation
            quarterly_financials_spark_df = quarterly_financials_spark_df.withColumn(
                "quarter_date", 
                to_date(col("quarter_date"), "yyyy-MM-dd")
            )
        else:
            quarterly_financials_spark_df = self.spark.createDataFrame([], SchemaHelper.get_quarterly_financials_schema())
            quarterly_financials_spark_df = quarterly_financials_spark_df.withColumn("quarter_date", to_date(col("quarter_date")))
        
        return earnings_spark_df, quarterly_spark_df, company_info_spark_df, sustainability_spark_df, quarterly_financials_spark_df

    def filter_earnings_data(self, earnings_df):
        """
        Filter earnings data based on date ranges and prepare for different database tables.
        
        Args:
            earnings_df (DataFrame): Spark DataFrame containing all earnings calendar data
        
        Returns:
            Tuple[DataFrame, DataFrame]: Tuple containing:
                - max_earnings_df: DataFrame with max (latest) earnings date per symbol
                - earnings_history_df: Complete DataFrame for historical storage
                
        Note:
            - Gets the maximum earnings date per symbol for the main earnings table
            - Keeps all historical data for the history table
            - Fills null values with 0.0 for consistency
        """
        # Convert earnings_date to date for comparison
        earnings_df = earnings_df.withColumn("earnings_date", to_date(col("earnings_date")))
        
        # Fill null values for the new schema
        earnings_df = earnings_df.fillna({
            'earnings_high': 0.0, 
            'earnings_low': 0.0, 
            'earnings_average': 0.0,
            'revenue_high': 0.0,
            'revenue_low': 0.0,
            'revenue_average': 0.0
        })
        
        # History table gets all data
        earnings_history_df = earnings_df
        
        # Main table gets the maximum (latest) earnings date per symbol
        window_spec = Window.partitionBy("Symbol").orderBy(col("earnings_date").desc())
        max_earnings_df = earnings_df.withColumn(
            "rank", row_number().over(window_spec)
        ).filter(col("rank") == 1).drop("rank")
        
        logger.info(f"✅ Filtered to max earnings dates per symbol: {max_earnings_df.count()} records")
        
        return max_earnings_df, earnings_history_df

    def save_earnings_data(self, max_earnings_df, earnings_history_df):
        """
        Save earnings data to MySQL database tables using upsert logic.
        Performs explicit validation to ensure no empty database operations.
        
        Args:
            max_earnings_df (DataFrame): Latest earnings data per symbol
            earnings_history_df (DataFrame): Complete historical earnings data
            
        Raises:
            Exception: If database save operation fails
            
        Note:
            - Uses upsert logic to preserve existing data when API calls fail
            - Only updates records where we have valid new data
            - Preserves existing records for symbols that failed to fetch data
            - Explicitly validates data before database operations
        """
        try:
            # Validate input data first
            max_earnings_count = max_earnings_df.count() if max_earnings_df else 0
            history_earnings_count = earnings_history_df.count() if earnings_history_df else 0
            
            if max_earnings_count == 0 and history_earnings_count == 0:
                logger.info("ℹ️ No earnings data to save - preserving existing data")
                return
            
            # Additional validation: check if DataFrames have actual symbols
            if max_earnings_count > 0:
                symbols_in_main = [row.Symbol for row in max_earnings_df.select("Symbol").distinct().collect()]
                if not symbols_in_main:
                    logger.warning("⚠️ Max earnings DataFrame has records but no symbols - skipping main table update")
                    max_earnings_count = 0
                else:
                    logger.info(f"📊 Main earnings table update: {max_earnings_count} records for symbols: {symbols_in_main}")
            
            if history_earnings_count > 0:
                symbols_in_history = [row.Symbol for row in earnings_history_df.select("Symbol").distinct().collect()]
                if not symbols_in_history:
                    logger.warning("⚠️ History earnings DataFrame has records but no symbols - skipping history table update")
                    history_earnings_count = 0
                else:
                    logger.info(f"📊 History earnings table update: {history_earnings_count} records for symbols: {symbols_in_history}")
            
            logger.info("💾 Saving earnings data using upsert logic with validation...")
            
            # Save history data using upsert approach
            if history_earnings_count > 0:
                logger.info("💾 Upserting earnings history data...")
                
                try:
                    # Try to load existing history data
                    existing_history_df = self.spark.read.format("jdbc").options(
                        url=self.url,
                        driver=self.sql_driver,
                        dbtable="stocks_earnings_history",
                        user=self.username,
                        password=self.password
                    ).load()
                    
                    # Convert earnings_date to date for comparison
                    existing_history_df = existing_history_df.withColumn("earnings_date", to_date(col("earnings_date")))
                    earnings_history_df = earnings_history_df.withColumn("earnings_date", to_date(col("earnings_date")))
                    
                    # Get existing combinations of Symbol and earnings_date
                    existing_combinations = existing_history_df.select("Symbol", "earnings_date").distinct()
                    
                    # Only insert new combinations that don't exist
                    new_history_data = earnings_history_df.alias("new").join(
                        existing_combinations.alias("existing"),
                        (col("new.Symbol") == col("existing.Symbol")) & 
                        (col("new.earnings_date") == col("existing.earnings_date")),
                        "left_anti"
                    )
                    
                    new_history_count = new_history_data.count()
                    if new_history_count > 0:
                        new_history_data.write.format('jdbc').options(
                            url=self.url,
                            driver=self.sql_driver,
                            dbtable="stocks_earnings_history",
                            user=self.username,
                            password=self.password
                        ).mode('append').save()
                        
                        logger.info(f"✅ Added {new_history_count} new records to stocks_earnings_history")
                    else:
                        logger.info("ℹ️ No new history records to add - all combinations already exist")
                    
                except Exception as history_error:
                    logger.warning(f"History table may not exist, creating with new data: {history_error}")
                    # If history table doesn't exist, create it with new data
                    earnings_history_df.write.format('jdbc').options(
                        url=self.url,
                        driver=self.sql_driver,
                        dbtable="stocks_earnings_history",
                        user=self.username,
                        password=self.password
                    ).mode('overwrite').save()
                    logger.info(f"✅ Created history table with {history_earnings_count} records")
            
            # Save max earnings to main table using upsert logic
            if max_earnings_count > 0:
                logger.info("💾 Upserting max earnings data to stocks_earnings table...")
                
                try:
                    # Try to load existing main table data
                    existing_main_df = self.spark.read.format("jdbc").options(
                        url=self.url,
                        driver=self.sql_driver,
                        dbtable="stocks_earnings",
                        user=self.username,
                        password=self.password
                    ).load()
                    
                    # Convert earnings_date to date for comparison
                    existing_main_df = existing_main_df.withColumn("earnings_date", to_date(col("earnings_date")))
                    max_earnings_df = max_earnings_df.withColumn("earnings_date", to_date(col("earnings_date")))
                    
                    # Get symbols from new data
                    new_symbols = [row.Symbol for row in max_earnings_df.select("Symbol").distinct().collect()]
                    
                    # Keep existing records for symbols NOT in the new data (preserve failed API calls)
                    existing_to_keep = existing_main_df.filter(~col("Symbol").isin(new_symbols))
                    
                    # Combine preserved existing data with new data
                    combined_df = existing_to_keep.union(max_earnings_df)
                    
                    # Final validation before save
                    final_count = combined_df.count()
                    if final_count > 0:
                        combined_df.write.format('jdbc').options(
                            url=self.url,
                            driver=self.sql_driver,
                            dbtable="stocks_earnings",
                            user=self.username,
                            password=self.password
                        ).mode('overwrite').save()
                        
                        logger.info(f"✅ Updated {max_earnings_count} symbols, preserved {existing_to_keep.count()} existing records")
                        logger.info(f"📊 Total records in main table: {final_count}")
                    else:
                        logger.warning("⚠️ Combined DataFrame is empty - skipping main table update")
                    
                except Exception as main_error:
                    logger.warning(f"Main table may not exist, creating with new data: {main_error}")
                    # If main table doesn't exist, create it with new data
                    max_earnings_df.write.format('jdbc').options(
                        url=self.url,
                        driver=self.sql_driver,
                        dbtable="stocks_earnings",
                        user=self.username,
                        password=self.password
                    ).mode('overwrite').save()
                    logger.info(f"✅ Created main table with {max_earnings_count} records")
                
                if max_earnings_count > 0 and max_earnings_count <= 20:  # Show preview for reasonable number of records
                    logger.info("📊 Max earnings preview:")
                    max_earnings_df.show(max_earnings_count, truncate=False)
                
        except Exception as e:
            logger.error(f"Error saving earnings data: {e}")
            raise

    def _deduplicate_and_filter_data(self, spark_df, table_name, primary_key_columns=None):
        """
        Helper method to deduplicate data and filter out existing records.
        
        Args:
            spark_df (DataFrame): Input Spark DataFrame
            table_name (str): Name of the target database table
            primary_key_columns (list): List of column names that form the primary key
        
        Returns:
            DataFrame: Deduplicated and filtered DataFrame ready for insertion
        """
        if primary_key_columns is None:
            primary_key_columns = ["Symbol", "quarter_date"]
        
        if spark_df.count() == 0:
            return spark_df
        
        logger.info(f"🔍 Deduplicating within new {table_name} data...")
        
        # Step 1: Deduplicate within the new data itself
        window_spec_new = Window.partitionBy(*primary_key_columns).orderBy(desc("total_revenue"))
        deduplicated_new_data = spark_df.withColumn(
            "row_num", row_number().over(window_spec_new)
        ).filter(col("row_num") == 1).drop("row_num")
        
        new_data_count = deduplicated_new_data.count()
        original_count = spark_df.count()
        if original_count > new_data_count:
            logger.info(f"🧹 Removed {original_count - new_data_count} duplicate records within new {table_name} data")
        
        # Step 2: Filter out existing combinations
        try:
            existing_df = self.spark.read.format("jdbc").options(
                url=self.url,
                driver=self.sql_driver,
                dbtable=table_name,
                user=self.username,
                password=self.password
            ).load()
            
            # Convert date columns if needed
            if "quarter_date" in existing_df.columns:
                existing_df = existing_df.withColumn("quarter_date", to_date(col("quarter_date")))
            
            # Get all existing primary key combinations
            existing_combinations = existing_df.select(*primary_key_columns).distinct()
            
            logger.info(f"📊 Found {existing_combinations.count()} existing combinations in {table_name}")
            
            # Anti-join to exclude records that already exist
            join_conditions = [
                col(f"new.{pk}") == col(f"existing.{pk}") 
                for pk in primary_key_columns
            ]
            
            new_data = deduplicated_new_data.alias("new").join(
                existing_combinations.alias("existing"),
                join_conditions,
                "left_anti"
            )
            
            logger.info(f"🔍 After filtering existing combinations: {new_data.count()} records remain for {table_name}")
            
        except Exception as e:
            logger.warning(f"Could not load existing {table_name} data: {e}")
            logger.info("Assuming this is the first run - using all deduplicated data")
            new_data = deduplicated_new_data
        
        return new_data

    def save_quarterly_data(self, quarterly_spark_df):
        """
        Save quarterly financial data with robust deduplication logic.
        
        Args:
            quarterly_spark_df (DataFrame): Spark DataFrame containing quarterly financial data
            
        Raises:
            Exception: If database save operation fails
            
        Note:
            - Implements comprehensive deduplication both within new data and against existing data
            - Prevents primary key constraint violations by excluding existing symbol-quarter combinations
            - Uses 'append' mode to preserve existing historical data
            - Added fallback to 'overwrite' mode if deduplication fails
        """
        try:
            if quarterly_spark_df.count() > 0:
                logger.info(f"📊 Processing {quarterly_spark_df.count()} quarterly records...")
                
                # Step 1: Deduplicate within the new data itself
                # Keep the most recent record for each Symbol-quarter_date combination
                logger.info("🔍 Deduplicating within new quarterly data...")
                window_spec_new = Window.partitionBy("Symbol", "quarter_date").orderBy(desc("total_revenue"))
                deduplicated_new_data = quarterly_spark_df.withColumn(
                    "row_num", row_number().over(window_spec_new)
                ).filter(col("row_num") == 1).drop("row_num")
                
                new_data_count = deduplicated_new_data.count()
                original_count = quarterly_spark_df.count()
                if original_count > new_data_count:
                    logger.info(f"🧹 Removed {original_count - new_data_count} duplicate records within new quarterly data")
                
                # Step 2: Load existing data and filter out already existing combinations
                try:
                    existing_df = self.spark.read.format("jdbc").options(
                        url=self.url,
                        driver=self.sql_driver,
                        dbtable=self.revenue_table,
                        user=self.username,
                        password=self.password
                    ).load()
                    
                    existing_df = existing_df.withColumn("quarter_date", to_date(col("quarter_date")))
                    
                    # Get all existing Symbol-quarter_date combinations
                    existing_combinations = existing_df.select("Symbol", "quarter_date").distinct()
                    
                    logger.info(f"📊 Found {existing_combinations.count()} existing symbol-quarter combinations in revenue table")
                    
                    # Anti-join to exclude records that already exist
                    new_data = deduplicated_new_data.alias("new").join(
                        existing_combinations.alias("existing"),
                        (col("new.Symbol") == col("existing.Symbol")) & 
                        (col("new.quarter_date") == col("existing.quarter_date")),
                        "left_anti"
                    )
                    
                    logger.info(f"🔍 After filtering existing combinations: {new_data.count()} quarterly records remain")
                    
                except Exception as e:
                    logger.warning(f"Could not load existing quarterly data: {e}")
                    logger.info("Assuming this is the first run - using all deduplicated data")
                    new_data = deduplicated_new_data
                
                # Step 3: Save the filtered new data
                if new_data.count() > 0:
                    logger.info(f"💾 Saving {new_data.count()} new quarterly records...")
                    new_data.show(10, truncate=False)
                    
                    try:
                        new_data.write.format('jdbc').options(
                            url=self.url,
                            driver=self.sql_driver,
                            dbtable=self.revenue_table,
                            user=self.username,
                            password=self.password
                        ).mode('append').save()
                        
                        logger.info("✅ Quarterly data saved successfully")
                        
                    except Exception as save_error:
                        logger.error(f"❌ Error saving quarterly data with append mode: {save_error}")
                        
                        # Fallback strategy: Try overwrite mode as last resort
                        logger.warning("⚠️ Attempting fallback strategy with overwrite mode for quarterly data...")
                        try:
                            # Load existing data and combine with new data
                            try:
                                existing_df = self.spark.read.format("jdbc").options(
                                    url=self.url,
                                    driver=self.sql_driver,
                                    dbtable=self.revenue_table,
                                    user=self.username,
                                    password=self.password
                                ).load()
                                
                                existing_df = existing_df.withColumn("quarter_date", to_date(col("quarter_date")))
                                
                                # Union existing and new data, then deduplicate
                                combined_df = existing_df.union(new_data)
                                
                                # Final deduplication on combined data
                                window_spec_final = Window.partitionBy("Symbol", "quarter_date").orderBy(desc("total_revenue"))
                                final_df = combined_df.withColumn(
                                    "row_num", row_number().over(window_spec_final)
                                ).filter(col("row_num") == 1).drop("row_num")
                                
                                final_df.write.format('jdbc').options(
                                    url=self.url,
                                    driver=self.sql_driver,
                                    dbtable=self.revenue_table,
                                    user=self.username,
                                    password=self.password
                                ).mode('overwrite').save()
                                
                                logger.info("✅ Quarterly data saved successfully using fallback overwrite mode")
                                
                            except Exception as fallback_error:
                                logger.error(f"❌ Fallback overwrite strategy also failed for quarterly data: {fallback_error}")
                                raise
                                
                        except Exception as final_error:
                            logger.error(f"❌ All save strategies failed for quarterly data: {final_error}")
                            raise
                            
                else:
                    logger.info("ℹ️ No new quarterly data to save (all records already exist)")
                    
            else:
                logger.info("ℹ️ No quarterly data to process")
                
        except Exception as e:
            logger.error(f"Error saving quarterly data: {e}")
            raise

    def save_company_info_data(self, company_info_spark_df):
        """
        Save company information data to MySQL database with upsert logic.
        Performs explicit validation to ensure no empty database operations.
        
        Args:
            company_info_spark_df (DataFrame): Spark DataFrame containing company information
            
        Raises:
            Exception: If database save operation fails
            
        Note:
            - Uses upsert logic to preserve existing data when API calls fail
            - Only updates records where we have valid new data
            - Preserves existing records for symbols that failed to fetch data
            - Explicitly validates data before database operations
        """
        try:
            # Validate input data first
            company_info_count = company_info_spark_df.count() if company_info_spark_df else 0
            
            if company_info_count == 0:
                logger.info("ℹ️ No company info data to save - preserving existing data")
                return
            
            # Additional validation: check if DataFrame has actual symbols and meaningful data
            symbols_in_data = [row.Symbol for row in company_info_spark_df.select("Symbol").distinct().collect()]
            if not symbols_in_data:
                logger.warning("⚠️ Company info DataFrame has records but no symbols - skipping database update")
                return
            
            # Validate that we have meaningful company data (not just empty values)
            sample_row = company_info_spark_df.first()
            if not sample_row or not sample_row.company_name or sample_row.company_name == sample_row.Symbol:
                logger.warning("⚠️ Company info appears to contain only empty/default values - skipping database update")
                return
            
            logger.info(f"💾 Upserting {company_info_count} company info records for symbols: {symbols_in_data}")
            
            # Show preview of company info
            logger.info("📊 Company info preview:")
            preview_count = min(company_info_count, 10)
            company_info_spark_df.select("Symbol", "company_name", "sector", "industry").show(preview_count, truncate=False)
            
            try:
                # Try to load existing data
                existing_df = self.spark.read.format("jdbc").options(
                    url=self.url,
                    driver=self.sql_driver,
                    dbtable=self.stock_details_table,
                    user=self.username,
                    password=self.password
                ).load()
                
                # Get symbols from new data
                new_symbols = symbols_in_data
                
                # Keep existing records for symbols NOT in the new data (preserve failed API calls)
                existing_to_keep = existing_df.filter(~col("Symbol").isin(new_symbols))
                
                # Combine preserved existing data with new data
                combined_df = existing_to_keep.union(company_info_spark_df)
                
                # Final validation before save
                final_count = combined_df.count()
                if final_count > 0:
                    combined_df.write.format('jdbc').options(
                        url=self.url,
                        driver=self.sql_driver,
                        dbtable=self.stock_details_table,
                        user=self.username,
                        password=self.password
                    ).mode('overwrite').save()
                    
                    logger.info(f"✅ Updated {company_info_count} symbols, preserved {existing_to_keep.count()} existing records")
                    logger.info(f"📊 Total records in company info table: {final_count}")
                else:
                    logger.warning("⚠️ Combined DataFrame is empty - skipping company info table update")
                
            except Exception as existing_error:
                logger.warning(f"Company info table may not exist, creating with new data: {existing_error}")
                # If table doesn't exist, create it with new data
                company_info_spark_df.write.format('jdbc').options(
                    url=self.url,
                    driver=self.sql_driver,
                    dbtable=self.stock_details_table,
                    user=self.username,
                    password=self.password
                ).mode('overwrite').save()
                logger.info(f"✅ Created company info table with {company_info_count} records")
            
        except Exception as e:
            logger.error(f"Error saving company info data: {e}")
            raise

    def save_sustainability_data(self, sustainability_spark_df):
        """
        Save sustainability ESG data to MySQL database with upsert logic.
        Performs explicit validation to ensure no empty database operations.
        
        Args:
            sustainability_spark_df (DataFrame): Spark DataFrame containing sustainability data
            
        Raises:
            Exception: If database save operation fails
            
        Note:
            - Uses upsert logic to preserve existing data when API calls fail
            - Only updates records where we have valid new data
            - Preserves existing records for symbols that failed to fetch data
            - Explicitly validates data before database operations
        """
        try:
            # Validate input data first
            sustainability_count = sustainability_spark_df.count() if sustainability_spark_df else 0
            
            if sustainability_count == 0:
                logger.info("ℹ️ No sustainability data to save - preserving existing data")
                return
            
            # Additional validation: check if DataFrame has actual symbols and meaningful data
            symbols_in_data = [row.Symbol for row in sustainability_spark_df.select("Symbol").distinct().collect()]
            if not symbols_in_data:
                logger.warning("⚠️ Sustainability DataFrame has records but no symbols - skipping database update")
                return
            
            # Validate that we have meaningful ESG data (not just empty/zero values)
            sample_row = sustainability_spark_df.first()
            if not sample_row or (sample_row.total_esg == 0 and sample_row.environment_score == 0 and sample_row.social_score == 0):
                logger.warning("⚠️ Sustainability data appears to contain only empty/zero values - skipping database update")
                return
            
            logger.info(f"🌱 Upserting {sustainability_count} sustainability records for symbols: {symbols_in_data}")
            
            # Show preview of sustainability data
            logger.info("📊 Sustainability data preview:")
            preview_count = min(sustainability_count, 10)
            sustainability_spark_df.select("Symbol", "total_esg", "environment_score", "social_score", "governance_score").show(preview_count, truncate=False)
            
            try:
                # Try to load existing data
                existing_df = self.spark.read.format("jdbc").options(
                    url=self.url,
                    driver=self.sql_driver,
                    dbtable=self.sustainability_table,
                    user=self.username,
                    password=self.password
                ).load()
                
                # Get symbols from new data
                new_symbols = symbols_in_data
                
                # Keep existing records for symbols NOT in the new data (preserve failed API calls)
                existing_to_keep = existing_df.filter(~col("Symbol").isin(new_symbols))
                
                # Combine preserved existing data with new data
                combined_df = existing_to_keep.union(sustainability_spark_df)
                
                # Final validation before save
                final_count = combined_df.count()
                if final_count > 0:
                    combined_df.write.format('jdbc').options(
                        url=self.url,
                        driver=self.sql_driver,
                        dbtable=self.sustainability_table,
                        user=self.username,
                        password=self.password
                    ).mode('overwrite').save()
                    
                    logger.info(f"✅ Updated {sustainability_count} symbols, preserved {existing_to_keep.count()} existing records")
                    logger.info(f"📊 Total records in sustainability table: {final_count}")
                else:
                    logger.warning("⚠️ Combined DataFrame is empty - skipping sustainability table update")
                
            except Exception as existing_error:
                logger.warning(f"Sustainability table may not exist, creating with new data: {existing_error}")
                # If table doesn't exist, create it with new data
                sustainability_spark_df.write.format('jdbc').options(
                    url=self.url,
                    driver=self.sql_driver,
                    dbtable=self.sustainability_table,
                    user=self.username,
                    password=self.password
                ).mode('overwrite').save()
                logger.info(f"✅ Created sustainability table with {sustainability_count} records")
            
        except Exception as e:
            logger.error(f"Error saving sustainability data: {e}")
            raise

    def save_quarterly_financials_data(self, quarterly_financials_spark_df):
        """
        Save quarterly financials data to MySQL database with robust deduplication logic.
        Performs explicit validation to ensure no empty database operations.
        
        Args:
            quarterly_financials_spark_df (DataFrame): Spark DataFrame containing quarterly financials data
            
        Raises:
            Exception: If database save operation fails
            
        Note:
            - Implements comprehensive deduplication both within new data and against existing data
            - Prevents primary key constraint violations by excluding existing symbol-quarter combinations
            - Uses 'append' mode to preserve existing historical data
            - Added fallback to 'overwrite' mode if deduplication fails
            - Explicitly validates data before database operations
        """
        try:
            # Validate input data first
            quarterly_financials_count = quarterly_financials_spark_df.count() if quarterly_financials_spark_df else 0
            
            if quarterly_financials_count == 0:
                logger.info("ℹ️ No quarterly financials data to process")
                return
            
            # Additional validation: check if DataFrame has actual symbols and meaningful data
            symbols_in_data = [row.Symbol for row in quarterly_financials_spark_df.select("Symbol").distinct().collect()]
            if not symbols_in_data:
                logger.warning("⚠️ Quarterly financials DataFrame has records but no symbols - skipping database update")
                return
            
            # Validate that we have meaningful financial data (not just empty/zero values)
            sample_row = quarterly_financials_spark_df.first()
            if not sample_row or (sample_row.total_revenue == 0 and sample_row.net_income == 0 and sample_row.ebitda == 0):
                logger.warning("⚠️ Quarterly financials data appears to contain only empty/zero values - skipping database update")
                return
            
            logger.info(f"📈 Processing {quarterly_financials_count} quarterly financials records for symbols: {symbols_in_data}")
            
            # Step 1: Deduplicate within the new data itself
            # Keep the most recent record for each Symbol-quarter_date combination
            logger.info("🔍 Deduplicating within new data...")
            window_spec_new = Window.partitionBy("Symbol", "quarter_date").orderBy(desc("total_revenue"))
            deduplicated_new_data = quarterly_financials_spark_df.withColumn(
                "row_num", row_number().over(window_spec_new)
            ).filter(col("row_num") == 1).drop("row_num")
            
            new_data_count = deduplicated_new_data.count()
            if quarterly_financials_count > new_data_count:
                logger.info(f"🧹 Removed {quarterly_financials_count - new_data_count} duplicate records within new data")
            
            # Step 2: Load existing data and filter out already existing combinations
            try:
                existing_df = self.spark.read.format("jdbc").options(
                    url=self.url,
                    driver=self.sql_driver,
                    dbtable=self.quarterly_financials_table,
                    user=self.username,
                    password=self.password
                ).load()
                
                existing_df = existing_df.withColumn("quarter_date", to_date(col("quarter_date")))
                
                # Get all existing Symbol-quarter_date combinations
                existing_combinations = existing_df.select("Symbol", "quarter_date").distinct()
                
                logger.info(f"📊 Found {existing_combinations.count()} existing symbol-quarter combinations")
                
                # Anti-join to exclude records that already exist
                new_data = deduplicated_new_data.alias("new").join(
                    existing_combinations.alias("existing"),
                    (col("new.Symbol") == col("existing.Symbol")) & 
                    (col("new.quarter_date") == col("existing.quarter_date")),
                    "left_anti"
                )
                
                logger.info(f"🔍 After filtering existing combinations: {new_data.count()} records remain")
                
            except Exception as e:
                logger.warning(f"Could not load existing quarterly financials data: {e}")
                logger.info("Assuming this is the first run - using all deduplicated data")
                new_data = deduplicated_new_data
            
            # Step 3: Save the filtered new data
            final_new_count = new_data.count()
            if final_new_count > 0:
                logger.info(f"💾 Saving {final_new_count} new quarterly financials records...")
                
                # Show preview
                logger.info("📊 Quarterly financials preview:")
                preview_count = min(final_new_count, 10)
                new_data.select("Symbol", "quarter_date", "total_revenue", "net_income", "ebitda").show(preview_count, truncate=False)
                
                try:
                    new_data.write.format('jdbc').options(
                        url=self.url,
                        driver=self.sql_driver,
                        dbtable=self.quarterly_financials_table,
                        user=self.username,
                        password=self.password
                    ).mode('append').save()
                    
                    logger.info("✅ Quarterly financials data saved successfully")
                    
                except Exception as save_error:
                    logger.error(f"❌ Error saving with append mode: {save_error}")
                    
                    # Fallback strategy: Try overwrite mode as last resort
                    logger.warning("⚠️ Attempting fallback strategy with overwrite mode...")
                    try:
                        # Load existing data and combine with new data
                        try:
                            existing_df = self.spark.read.format("jdbc").options(
                                url=self.url,
                                driver=self.sql_driver,
                                dbtable=self.quarterly_financials_table,
                                user=self.username,
                                password=self.password
                            ).load()
                            
                            existing_df = existing_df.withColumn("quarter_date", to_date(col("quarter_date")))
                            
                            # Union existing and new data, then deduplicate
                            combined_df = existing_df.union(new_data)
                            
                            # Final deduplication on combined data
                            window_spec_final = Window.partitionBy("Symbol", "quarter_date").orderBy(desc("total_revenue"))
                            final_df = combined_df.withColumn(
                                "row_num", row_number().over(window_spec_final)
                            ).filter(col("row_num") == 1).drop("row_num")
                            
                            final_df.write.format('jdbc').options(
                                url=self.url,
                                driver=self.sql_driver,
                                dbtable=self.quarterly_financials_table,
                                user=self.username,
                                password=self.password
                            ).mode('overwrite').save()
                            
                            logger.info("✅ Quarterly financials data saved successfully using fallback overwrite mode")
                            
                        except Exception as fallback_error:
                            logger.error(f"❌ Fallback overwrite strategy also failed: {fallback_error}")
                            raise
                            
                    except Exception as final_error:
                        logger.error(f"❌ All save strategies failed: {final_error}")
                        raise
                        
            else:
                logger.info("ℹ️ No new quarterly financials data to save (all records already exist)")
                
        except Exception as e:
            logger.error(f"Error saving quarterly financials data: {e}")
            raise

    def run(self):
        """
        Execute the main data loading workflow using multi-threading.
        
        This method orchestrates the entire data loading process:
        1. Retrieves stock symbols from config or database
        2. Fetches earnings and quarterly data for each symbol using ThreadPoolExecutor
        3. Processes and cleans the raw data
        4. Converts data to Spark DataFrames
        5. Applies filtering and deduplication logic
        6. Saves processed data to MySQL database tables
        
        Raises:
            Exception: If any critical error occurs during execution
            
        Note:
            - Uses multi-threading to speed up data fetching (I/O bound operations)
            - Ensures Spark session is properly closed on completion
            - Provides comprehensive logging throughout the process
            - Handles graceful error recovery where possible
        """
        try:
            logger.info("🚀 Starting Stock Data Loader with Multi-Threading...")
            
            # Get symbols to process
            all_symbols = self.get_stock_symbols()
            if not all_symbols:
                logger.error("No symbols to process. Exiting.")
                return
            
            # Smart filtering: Skip symbols with recent future earnings data to optimize API calls
            # But still allow updates for symbols with potentially stale earnings data
            symbols = self.smart_filter_symbols_for_updates(all_symbols, days_threshold=self.smart_filter_days)
            if not symbols:
                logger.info("🎉 All symbols have been updated recently! No processing needed.")
                return
            
            # Log optimization results
            skipped_count = len(all_symbols) - len(symbols)
            if skipped_count > 0:
                time_saved_estimate = skipped_count * 6  # ~6 seconds per symbol saved
                logger.info(f"⚡ Smart filtering optimization:")
                logger.info(f"   Skipped: {skipped_count}/{len(all_symbols)} symbols ({(skipped_count/len(all_symbols)*100):.1f}%)")
                logger.info(f"   Estimated time saved: ~{time_saved_estimate} seconds")
            
            # Clear data containers
            self.all_earnings_data = []
            self.all_quarterly_data = []
            self.all_company_info_data = []
            self.all_sustainability_data = []
            self.all_quarterly_financials_data = []
            
            # Process symbols using ThreadPoolExecutor
            logger.info(f"🧵 Processing {len(symbols)} symbols using {self.max_workers} threads...")
            
            start_time = time.time()
            results = []
            with ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="YFinance") as executor:
                # Submit all tasks
                future_to_symbol = {executor.submit(self.process_single_ticker, symbol): symbol 
                                   for symbol in symbols}
                
                # Collect results as they complete
                completed_count = 0
                total_count = len(symbols)
                
                for future in as_completed(future_to_symbol, timeout=self.timeout_seconds * total_count):
                    symbol = future_to_symbol[future]
                    completed_count += 1
                    
                    try:
                        result = future.result(timeout=self.timeout_seconds)
                        results.append(result)
                        
                        # Log progress
                        progress = (completed_count / total_count) * 100
                        status = "✅" if result['success'] else "⚠️"
                        logger.info(f"📊 Progress: {completed_count}/{total_count} ({progress:.1f}%) - {symbol} {status}")
                        
                    except TimeoutError:
                        logger.error(f"⏰ Timeout processing {symbol} after {self.timeout_seconds} seconds")
                        # Add failed result
                        results.append({
                            'symbol': symbol,
                            'success': False,
                            'error': 'Timeout'
                        })
                    except Exception as e:
                        logger.error(f"❌ Error processing {symbol}: {e}")
                        # Add failed result
                        results.append({
                            'symbol': symbol,
                            'success': False,
                            'error': str(e)
                        })
            
            # Calculate performance metrics
            end_time = time.time()
            total_time = end_time - start_time
            avg_time_per_symbol = total_time / len(symbols) if symbols else 0
            
            # Collect all results thread-safely
            logger.info("🔄 Collecting results from all threads...")
            self.collect_ticker_results(results)
            
            # Report summary
            successful_symbols = [r['symbol'] for r in results if r['success']]
            failed_symbols = [r['symbol'] for r in results if not r['success']]
            
            logger.info(f"⏱️ Performance Summary:")
            logger.info(f"   Total time: {total_time:.2f} seconds")
            logger.info(f"   Average per symbol: {avg_time_per_symbol:.2f} seconds")
            logger.info(f"   Throughput: {len(symbols)/total_time:.2f} symbols/second")
            logger.info(f"✅ Successfully processed: {len(successful_symbols)} symbols")
            if failed_symbols:
                logger.warning(f"⚠️ Failed to process: {len(failed_symbols)} symbols: {failed_symbols}")
            
            # Process the collected data
            logger.info("🔄 Processing collected data...")
            
            earnings_df = self.process_earnings_data(self.all_earnings_data)
            quarterly_df = self.process_quarterly_data(self.all_quarterly_data)
            company_info_df = self.process_company_info_data(self.all_company_info_data)
            sustainability_df = self.process_sustainability_data(self.all_sustainability_data)
            quarterly_financials_df = self.process_quarterly_financials_data(self.all_quarterly_financials_data)
            
            # Convert to Spark DataFrames
            earnings_spark_df, quarterly_spark_df, company_info_spark_df, sustainability_spark_df, quarterly_financials_spark_df = self.create_spark_dataframes(
                earnings_df, quarterly_df, company_info_df, sustainability_df, quarterly_financials_df)
            
            # Process and save earnings data
            if earnings_spark_df.count() > 0:
                max_earnings_df, earnings_history_df = self.filter_earnings_data(earnings_spark_df)
                self.save_earnings_data(max_earnings_df, earnings_history_df)
            else:
                logger.info("ℹ️ No earnings data to process")
            
            # Skip saving simple quarterly data since we have detailed quarterly financials
            # (The detailed quarterly_financials_data contains the same revenue info plus much more)
            logger.info("ℹ️ Skipping simple quarterly data save - detailed quarterly financials will be saved instead")
            
            # Save company info data
            if company_info_spark_df.count() > 0:
                self.save_company_info_data(company_info_spark_df)
            else:
                logger.info("ℹ️ No company info data to process")
            
            # Save sustainability data
            if sustainability_spark_df.count() > 0:
                self.save_sustainability_data(sustainability_spark_df)
            else:
                logger.info("ℹ️ No sustainability data to process")
            
            # Save quarterly financials data
            if quarterly_financials_spark_df.count() > 0:
                self.save_quarterly_financials_data(quarterly_financials_spark_df)
            else:
                logger.info("ℹ️ No quarterly financials data to process")
            
            logger.info("✅ Stock Data Loader completed successfully!")
            
        except Exception as e:
            logger.error(f"❌ Error in main execution: {e}")
            raise
        finally:
            # Clean up
            if hasattr(self, 'spark'):
                self.spark.stop()
                logger.info("🛑 Spark session stopped")

    def _check_global_rate_limit(self, thread_id: str = None):
        """
        Check if we need to wait due to recent rate limits hit by any thread.
        This helps coordinate all threads to slow down when rate limits are encountered.
        
        Args:
            thread_id (str, optional): Thread identifier for logging
        """
        current_time = time.time()
        
        with self.rate_limit_lock:
            if self.last_rate_limit_time > 0:
                time_since_rate_limit = current_time - self.last_rate_limit_time
                
                if time_since_rate_limit < self.global_rate_limit_cooldown:
                    wait_time = self.global_rate_limit_cooldown - time_since_rate_limit
                    if thread_id:
                        logger.info(f"🚦 [{thread_id}] Global rate limit cooldown: waiting {wait_time:.1f}s")
                    else:
                        logger.info(f"🚦 Global rate limit cooldown: waiting {wait_time:.1f}s")
                    time.sleep(wait_time)
    
    def _record_rate_limit_hit(self, thread_id: str = None):
        """
        Record that a rate limit was hit to coordinate global slowdown.
        
        Args:
            thread_id (str, optional): Thread identifier for logging
        """
        with self.rate_limit_lock:
            self.last_rate_limit_time = time.time()
            if thread_id:
                logger.warning(f"🚦 [{thread_id}] Rate limit recorded - all threads will slow down")
            else:
                logger.warning(f"🚦 Rate limit recorded - all threads will slow down")


def main():
    """
    Entry point for the script when run as a standalone module.
    
    Creates a StockDataLoader instance and executes the main workflow.
    Handles fatal errors by logging them and exiting with error code 1.
    
    Usage:
        python stock_earning_loader.py
    """
    try:
        loader = StockDataLoader()
        loader.run()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()