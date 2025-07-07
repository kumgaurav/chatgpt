"""
Stock Data Loader for Yahoo Finance Integration with PySpark, MySQL, and File Caching

This module provides a comprehensive solution for fetching stock earnings calendar and 
quarterly financial data from Yahoo Finance, processing it with PySpark, and storing it 
in MySQL database tables. The loader features intelligent file caching to minimize 
expensive API calls and supports multiple download modes.

Features:
    - File-based caching system to reduce Yahoo Finance API costs
    - Three download modes: download (always fetch), skip (cache only), auto (smart caching)
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
Date: January 15, 2025
Version: 3.0
License: MIT

Dependencies:
    - yfinance: Yahoo Finance API wrapper (updated for calendar data)
    - pyspark: Apache Spark Python API
    - pandas: Data manipulation library
    - mysql-connector-j: MySQL JDBC driver

Usage:
    # Download fresh data
    python stock_earning_loader.py --download-mode download
    
    # Use only cached data (no API calls)
    python stock_earning_loader.py --download-mode skip
    
    # Smart mode (use cache if fresh, otherwise download)
    python stock_earning_loader.py --download-mode auto
    
    # Programmatic usage
    loader = StockDataLoader()
    loader.run()
    
Configuration:
    Requires conf/config.ini with sections for mysql, stocks, download, and spark settings.
    
Cache Structure:
    data/earnings/
    ├── earnings/           # Earnings calendar data
    ├── company_info/       # Company information and fundamentals
    ├── sustainability/     # ESG sustainability data
    └── quarterly_financials/  # Detailed quarterly financial statements
    
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
import argparse

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
    
    # File caching system
    DATA_CACHE_DIR = "data/earnings"
    EARNINGS_CACHE_SUBDIR = "earnings"
    COMPANY_INFO_CACHE_SUBDIR = "company_info"
    SUSTAINABILITY_CACHE_SUBDIR = "sustainability"
    QUARTERLY_FINANCIALS_CACHE_SUBDIR = "quarterly_financials"
    
    # Cache file extensions
    CACHE_FILE_EXTENSION = ".json"
    
    # Download modes
    DOWNLOAD_MODE_DOWNLOAD = "download"
    DOWNLOAD_MODE_SKIP = "skip"
    DOWNLOAD_MODE_AUTO = "auto"  # Download only if file doesn't exist or is old
    
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


class FileManager:
    """Utility class for managing cached data files."""
    
    @staticmethod
    def ensure_cache_directories():
        """Create cache directories if they don't exist."""
        import os
        
        base_dir = Constants.DATA_CACHE_DIR
        subdirs = [
            Constants.EARNINGS_CACHE_SUBDIR,
            Constants.COMPANY_INFO_CACHE_SUBDIR,
            Constants.SUSTAINABILITY_CACHE_SUBDIR,
            Constants.QUARTERLY_FINANCIALS_CACHE_SUBDIR
        ]
        
        # Create base directory
        os.makedirs(base_dir, exist_ok=True)
        
        # Create subdirectories
        for subdir in subdirs:
            full_path = os.path.join(base_dir, subdir)
            os.makedirs(full_path, exist_ok=True)
            
        logger.info(f"📁 Cache directories ensured at: {base_dir}")
    
    @staticmethod
    def get_cache_file_path(symbol: str, data_type: str) -> str:
        """Get the cache file path for a symbol and data type."""
        import os
        
        subdir_map = {
            'earnings': Constants.EARNINGS_CACHE_SUBDIR,
            'company_info': Constants.COMPANY_INFO_CACHE_SUBDIR,
            'sustainability': Constants.SUSTAINABILITY_CACHE_SUBDIR,
            'quarterly_financials': Constants.QUARTERLY_FINANCIALS_CACHE_SUBDIR
        }
        
        subdir = subdir_map.get(data_type, data_type)
        filename = f"{symbol}{Constants.CACHE_FILE_EXTENSION}"
        
        return os.path.join(Constants.DATA_CACHE_DIR, subdir, filename)
    
    @staticmethod
    def save_data_to_cache(symbol: str, data_type: str, data: Any) -> bool:
        """Save data to cache file."""
        import json
        import os
        from datetime import datetime
        
        try:
            file_path = FileManager.get_cache_file_path(symbol, data_type)
            
            # Prepare data with metadata
            cache_data = {
                'symbol': symbol,
                'data_type': data_type,
                'timestamp': datetime.now().isoformat(),
                'data': data
            }
            
            # Ensure directory exists
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            # Save to file
            with open(file_path, 'w') as f:
                json.dump(cache_data, f, indent=2, default=str)
            
            logger.debug(f"💾 Cached {data_type} data for {symbol} to {file_path}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to cache {data_type} data for {symbol}: {e}")
            return False
    
    @staticmethod
    def load_data_from_cache(symbol: str, data_type: str) -> Optional[Any]:
        """Load data from cache file."""
        import json
        import os
        
        try:
            file_path = FileManager.get_cache_file_path(symbol, data_type)
            
            if not os.path.exists(file_path):
                return None
            
            with open(file_path, 'r') as f:
                cache_data = json.load(f)
            
            logger.debug(f"📂 Loaded {data_type} data for {symbol} from cache")
            return cache_data.get('data')
            
        except Exception as e:
            logger.warning(f"⚠️ Failed to load {data_type} data for {symbol} from cache: {e}")
            return None
    
    @staticmethod
    def is_cache_file_fresh(symbol: str, data_type: str, max_age_days: int = 7) -> bool:
        """Check if cache file exists and is fresh."""
        import os
        import json
        from datetime import datetime, timedelta
        
        try:
            file_path = FileManager.get_cache_file_path(symbol, data_type)
            
            if not os.path.exists(file_path):
                return False
            
            with open(file_path, 'r') as f:
                cache_data = json.load(f)
            
            timestamp_str = cache_data.get('timestamp')
            if not timestamp_str:
                return False
            
            timestamp = datetime.fromisoformat(timestamp_str)
            age = datetime.now() - timestamp
            
            return age.days <= max_age_days
            
        except Exception as e:
            logger.warning(f"⚠️ Failed to check cache freshness for {symbol} {data_type}: {e}")
            return False
    
    @staticmethod
    def get_cache_stats() -> Dict[str, int]:
        """Get statistics about cached files."""
        import os
        
        stats = {}
        
        try:
            base_dir = Constants.DATA_CACHE_DIR
            
            if not os.path.exists(base_dir):
                return {'total_files': 0}
            
            subdirs = [
                Constants.EARNINGS_CACHE_SUBDIR,
                Constants.COMPANY_INFO_CACHE_SUBDIR,
                Constants.SUSTAINABILITY_CACHE_SUBDIR,
                Constants.QUARTERLY_FINANCIALS_CACHE_SUBDIR
            ]
            
            total_files = 0
            for subdir in subdirs:
                subdir_path = os.path.join(base_dir, subdir)
                if os.path.exists(subdir_path):
                    files = [f for f in os.listdir(subdir_path) if f.endswith(Constants.CACHE_FILE_EXTENSION)]
                    count = len(files)
                    stats[subdir] = count
                    total_files += count
                else:
                    stats[subdir] = 0
            
            stats['total_files'] = total_files
            return stats
            
        except Exception as e:
            logger.error(f"❌ Failed to get cache stats: {e}")
            return {'error': str(e)}


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
        
        # Download mode configuration - default to SKIP to avoid API costs
        self.download_mode = self.config.get('download', 'mode', fallback=Constants.DOWNLOAD_MODE_SKIP)
        self.cache_max_age_days = int(self.config.get('download', 'cache_max_age_days', fallback='7'))
        
        # Symbol processing limit (can be overridden by command line)
        self.symbol_limit = None
        
        # Single symbol processing (can be overridden by command line)
        self.single_symbol = None
        
        # Force reload flag (bypasses smart filtering)
        self.force_reload = False
        
        # Debug: Log what mode was actually loaded
        logger.info(f"🔧 DEBUG: Loaded download_mode = '{self.download_mode}' from config")
        
        # Safety check: Force skip mode if any issues
        if self.download_mode not in [Constants.DOWNLOAD_MODE_DOWNLOAD, Constants.DOWNLOAD_MODE_SKIP, Constants.DOWNLOAD_MODE_AUTO]:
            logger.warning(f"⚠️ Invalid download mode '{self.download_mode}', forcing SKIP mode")
            self.download_mode = Constants.DOWNLOAD_MODE_SKIP
        
        # Ensure cache directories exist
        FileManager.ensure_cache_directories()
        
        # Log cache statistics
        cache_stats = FileManager.get_cache_stats()
        logger.info(f"📊 Cache Statistics: {cache_stats}")
        
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
        
        logger.info(f"🔧 DEBUG: Looking for config file at: {config_file}")
        
        if not os.path.exists(config_file):
            raise FileNotFoundError(f"Configuration file not found: {config_file}")
            
        logger.info(f"🔧 DEBUG: Config file exists, reading...")
        config.read(config_file)
        logger.info(f"Configuration loaded. Sections: {config.sections()}")
        
        # Debug: Check if download section exists
        if config.has_section('download'):
            logger.info(f"🔧 DEBUG: [download] section found with options: {dict(config['download'])}")
        else:
            logger.warning(f"⚠️ DEBUG: [download] section NOT found in config - will use default SKIP mode")
        
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
        logger.info(f"💾 Download Mode: {self.download_mode} (cache max age: {self.cache_max_age_days} days)")
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
        logger.info(f"  - download.mode: {Constants.DOWNLOAD_MODE_DOWNLOAD}, {Constants.DOWNLOAD_MODE_SKIP}, or {Constants.DOWNLOAD_MODE_AUTO} (default)")
        logger.info(f"  - download.cache_max_age_days: Maximum age of cache files in days (default: 7)")
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

    def get_earnings_data(self, ticker: yf.Ticker, symbol: str, use_cache: bool = True) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch earnings data from cache or Yahoo Finance calendar including estimates and ranges.
        
        Args:
            ticker (yf.Ticker): Yahoo Finance ticker object
            symbol (str): Stock symbol for logging and data association
            use_cache (bool): Whether to use cached data if available
        
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
        # Try to load from cache first
        if use_cache:
            cached_data = FileManager.load_data_from_cache(symbol, 'earnings')
            if cached_data:
                logger.info(f"📂 Using cached earnings data for {symbol}")
                return cached_data
            
            # In SKIP mode, never fall back to API if cache is missing
            if self.download_mode == Constants.DOWNLOAD_MODE_SKIP:
                logger.info(f"💾 Skip mode: No cached earnings data for {symbol}, skipping API call")
                return None
        
        # If not using cache or cache miss (and not in skip mode), fetch from API
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

                # Cache the successful data
                FileManager.save_data_to_cache(symbol, 'earnings', earnings_data_list)
                
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

    def get_company_info(self, ticker: yf.Ticker, symbol: str, use_cache: bool = True) -> Optional[Dict[str, Any]]:
        """
        Fetch company information from cache or Yahoo Finance info data.
        
        Args:
            ticker (yf.Ticker): Yahoo Finance ticker object
            symbol (str): Stock symbol for logging and data association
            use_cache (bool): Whether to use cached data if available
        
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
        # Try to load from cache first
        if use_cache:
            cached_data = FileManager.load_data_from_cache(symbol, 'company_info')
            if cached_data:
                logger.info(f"📂 Using cached company info for {symbol}")
                return cached_data
            
            # In SKIP mode, never fall back to API if cache is missing
            if self.download_mode == Constants.DOWNLOAD_MODE_SKIP:
                logger.info(f"💾 Skip mode: No cached company info for {symbol}, skipping API call")
                return None
        
        # If not using cache or cache miss (and not in skip mode), fetch from API
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
                
                # Cache the successful data
                FileManager.save_data_to_cache(symbol, 'company_info', company_info)
                
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

    def get_sustainability_data(self, ticker: yf.Ticker, symbol: str, use_cache: bool = True) -> Optional[Dict[str, Any]]:
        """
        Fetch ESG (Environmental, Social, Governance) sustainability data from cache or Yahoo Finance.
        
        Args:
            ticker (yf.Ticker): Yahoo Finance ticker object
            symbol (str): Stock symbol for logging and data association
            use_cache (bool): Whether to use cached data if available
        
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
        # Try to load from cache first
        if use_cache:
            cached_data = FileManager.load_data_from_cache(symbol, 'sustainability')
            if cached_data:
                logger.info(f"📂 Using cached sustainability data for {symbol}")
                return cached_data
            
            # In SKIP mode, never fall back to API if cache is missing
            if self.download_mode == Constants.DOWNLOAD_MODE_SKIP:
                logger.info(f"💾 Skip mode: No cached sustainability data for {symbol}, skipping API call")
                return None
        
        # If not using cache or cache miss (and not in skip mode), fetch from API
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
                
                # Cache the successful data
                FileManager.save_data_to_cache(symbol, 'sustainability', sustainability_info)
                
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

    def get_quarterly_financials_data(self, ticker: yf.Ticker, symbol: str, use_cache: bool = True) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch detailed quarterly financial statements from cache or Yahoo Finance.
        
        Args:
            ticker (yf.Ticker): Yahoo Finance ticker object
            symbol (str): Stock symbol for logging and data association
            use_cache (bool): Whether to use cached data if available
        
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
        # Try to load from cache first
        if use_cache:
            cached_data = FileManager.load_data_from_cache(symbol, 'quarterly_financials')
            if cached_data:
                logger.info(f"📂 Using cached quarterly financials for {symbol}")
                return cached_data
            
            # In SKIP mode, never fall back to API if cache is missing
            if self.download_mode == Constants.DOWNLOAD_MODE_SKIP:
                logger.info(f"💾 Skip mode: No cached quarterly financials for {symbol}, skipping API call")
                return None
        
        # If not using cache or cache miss (and not in skip mode), fetch from API
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
                
                # Cache the successful data
                FileManager.save_data_to_cache(symbol, 'quarterly_financials', financials_data_list)
                
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

    def get_quarterly_financials(self, ticker: yf.Ticker, symbol: str, use_cache: bool = True) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch quarterly financial data, specifically total revenue, from cache or Yahoo Finance.
        
        Args:
            ticker (yf.Ticker): Yahoo Finance ticker object
            symbol (str): Stock symbol for logging and data association
            use_cache (bool): Whether to use cached data if available
        
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
        # Try to load from cache first
        if use_cache:
            cached_data = FileManager.load_data_from_cache(symbol, 'quarterly')
            if cached_data:
                logger.info(f"📂 Using cached quarterly data for {symbol}")
                return cached_data
            
            # In SKIP mode, never fall back to API if cache is missing
            if self.download_mode == Constants.DOWNLOAD_MODE_SKIP:
                logger.info(f"💾 Skip mode: No cached quarterly data for {symbol}, skipping API call")
                return None
        
        # If not using cache or cache miss (and not in skip mode), fetch from API
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

                # Cache the successful data
                FileManager.save_data_to_cache(symbol, 'quarterly', financial_data_list)
                
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
            logger.info(f"🔍 DEBUG: Input symbols: {symbols}")
            
            # Load existing earnings data
            existing_earnings_df = self.spark.read.format("jdbc").options(
                url=self.url,
                driver=self.sql_driver,
                dbtable=self.earnings_table,
                user=self.username,
                password=self.password
            ).load()
            
            existing_count = existing_earnings_df.count()
            if existing_count == 0:
                logger.info("📋 No existing earnings data found - processing all symbols")
                return symbols
            
            logger.info(f"🔍 DEBUG: Found {existing_count} existing earnings records")
            
            # Get current date
            from datetime import date, timedelta
            today = date.today()
            
            # Convert earnings_date to date for comparison
            existing_earnings_df = existing_earnings_df.withColumn(
                "earnings_date", to_date(col("earnings_date"))
            )
            
            # Debug: show existing data
            logger.info("🔍 DEBUG: Sample of existing earnings data:")
            existing_earnings_df.select("Symbol", "earnings_date").show(10, truncate=False)
            
            # Find symbols that have ANY future earnings dates (skip these for API optimization)
            symbols_with_future_earnings_df = existing_earnings_df.filter(
                col("earnings_date") > lit(today.strftime('%Y-%m-%d'))
            ).select("Symbol").distinct()
            
            # Get list of symbols with future earnings (skip these)
            symbols_to_skip = [row.Symbol for row in symbols_with_future_earnings_df.collect()]
            
            logger.info(f"🔍 DEBUG: Found {len(symbols_to_skip)} symbols with future earnings dates")
            if symbols_to_skip:
                logger.info(f"🔍 DEBUG: Symbols with future earnings: {symbols_to_skip}")
            
            # Optional: Allow updates for very old earnings data (if needed)
            # This allows refreshing earnings data that might be stale
            if days_threshold > 0:
                old_threshold = today - timedelta(days=days_threshold)
                logger.info(f"🔍 DEBUG: Checking for old data before {old_threshold}")
                
                # Find symbols with very old earnings data that might need refreshing
                very_old_earnings_df = existing_earnings_df.filter(
                    col("earnings_date") < lit(old_threshold.strftime('%Y-%m-%d'))
                ).select("Symbol").distinct()
                
                symbols_needing_refresh = [row.Symbol for row in very_old_earnings_df.collect()]
                
                logger.info(f"🔍 DEBUG: Found {len(symbols_needing_refresh)} symbols with old earnings data")
                if symbols_needing_refresh:
                    logger.info(f"🔍 DEBUG: Symbols needing refresh: {symbols_needing_refresh}")
                
                # Remove symbols that need refresh from the skip list
                symbols_to_skip_before_refresh = symbols_to_skip.copy()
                symbols_to_skip = [symbol for symbol in symbols_to_skip 
                                 if symbol not in symbols_needing_refresh]
                
                refresh_recovered = len(symbols_to_skip_before_refresh) - len(symbols_to_skip)
                if refresh_recovered > 0:
                    logger.info(f"🔄 Allowing refresh for {refresh_recovered} symbols with old earnings data")
            
            # Filter out symbols that can be skipped for API optimization
            symbols_to_process = [symbol for symbol in symbols 
                                if symbol not in symbols_to_skip]
            
            # Additional debugging: check which symbols are being processed vs skipped
            symbols_in_input_but_not_in_db = [s for s in symbols if s not in [row.Symbol for row in existing_earnings_df.select("Symbol").distinct().collect()]]
            if symbols_in_input_but_not_in_db:
                logger.info(f"🔍 DEBUG: Symbols not in database (will be processed): {symbols_in_input_but_not_in_db}")
            
            # Log the filtering results
            if symbols_to_skip:
                logger.info(f"⚡ API Optimization: Skipping {len(symbols_to_skip)} symbols with future earnings dates")
                logger.info(f"   Symbols skipped: {symbols_to_skip}")
            
            if symbols_to_process:
                logger.info(f"🎯 Processing {len(symbols_to_process)} symbols that need updates")
                logger.info(f"   Will process: {symbols_to_process}")
            else:
                logger.info("✅ All symbols have future earnings data - API calls optimized!")
            
            # Critical validation: ensure we're not accidentally filtering out all symbols
            if len(symbols) > 0 and len(symbols_to_process) == 0:
                logger.warning("🚨 WARNING: All symbols were filtered out - this might indicate an issue!")
                logger.warning("🚨 Check if the smart filtering logic is too aggressive")
                logger.warning("🚨 Consider adjusting the days_threshold or checking the data")
            
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
        
        # Determine cache usage based on download mode
        use_cache = True
        if self.download_mode == Constants.DOWNLOAD_MODE_DOWNLOAD:
            use_cache = False  # Force download from API
        elif self.download_mode == Constants.DOWNLOAD_MODE_SKIP:
            use_cache = True   # Only use cache, don't download
        elif self.download_mode == Constants.DOWNLOAD_MODE_AUTO:
            # Use cache if fresh, otherwise download
            use_cache = self._should_use_cache_for_symbol(symbol)
        
        # Check if we need to wait due to recent rate limits from other threads
        self._check_global_rate_limit(thread_id)
        
        for attempt in range(self.max_retries + 1):  # 0, 1, 2, 3 (4 total attempts)
            try:
                if attempt > 0:
                    logger.info(f"🔄 [{thread_id}] Retry attempt {attempt}/{self.max_retries} for {symbol}...")
                else:
                    cache_mode = "📂 cache" if use_cache else "🌐 API"
                    logger.info(f"🧵 [{thread_id}] Processing {symbol} ({cache_mode})...")
                
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
                    earnings_data = self.get_earnings_data(ticker, symbol, use_cache)
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
                    quarterly_data = self.get_quarterly_financials(ticker, symbol, use_cache)
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
                    company_info = self.get_company_info(ticker, symbol, use_cache)
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
                    sustainability_data = self.get_sustainability_data(ticker, symbol, use_cache)
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
                    quarterly_financials_data = self.get_quarterly_financials_data(ticker, symbol, use_cache)
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

    def _should_use_cache_for_symbol(self, symbol: str) -> bool:
        """
        Determine whether to use cache for a symbol based on freshness and download mode.
        
        Args:
            symbol (str): Stock symbol to check
            
        Returns:
            bool: True if cache should be used, False if data should be downloaded
        """
        # Check if any cache files exist and are fresh
        data_types = ['earnings', 'company_info', 'sustainability', 'quarterly_financials']
        
        for data_type in data_types:
            if FileManager.is_cache_file_fresh(symbol, data_type, self.cache_max_age_days):
                # At least one cache file is fresh, use cache
                return True
        
        # No fresh cache files found, download from API
        return False

    def get_symbols_needing_download(self, all_symbols: List[str]) -> List[str]:
        """
        Get symbols that need downloading based on cache freshness and future earnings.
        
        Only downloads for symbols that:
        1. Do not have future earnings dates in the database (unless force_reload=True)
        2. Are active stock symbols
        3. Don't have fresh cache files (for auto mode)
        
        Args:
            all_symbols (List[str]): List of all symbols to check
            
        Returns:
            List[str]: List of symbols that need downloading
        """
        logger.info(f"🔧 DEBUG: get_symbols_needing_download called with mode='{self.download_mode}', force_reload={self.force_reload}")
        
        if self.download_mode == Constants.DOWNLOAD_MODE_SKIP:
            # Check if force reload is enabled
            if self.force_reload:
                logger.info("🔄 Force reload enabled - will process ALL symbols with cache files (ignoring database state)")
                # For force reload, check ALL symbols for cache files, not just filtered ones
                symbols_to_check = all_symbols
            else:
                # FIXED: In SKIP mode, still process symbols that have cache files
                # First apply smart filtering to get symbols that need updates
                symbols_to_check = self.smart_filter_symbols_for_updates(all_symbols, days_threshold=self.smart_filter_days)
            
            # Then filter to only include symbols that have cache files
            symbols_with_cache = []
            for symbol in symbols_to_check:
                # Check if symbol has any cache files
                has_cache = False
                data_types = ['earnings', 'company_info', 'sustainability', 'quarterly_financials']
                for data_type in data_types:
                    if FileManager.is_cache_file_fresh(symbol, data_type, self.cache_max_age_days):
                        has_cache = True
                        break
                
                if has_cache:
                    symbols_with_cache.append(symbol)
            
            if self.force_reload:
                logger.info(f"🔄 FORCE RELOAD mode: Found {len(symbols_with_cache)} symbols with cache files out of {len(symbols_to_check)} total symbols")
                logger.info(f"🔄 FORCE RELOAD mode: Will reload ALL cached symbols (including those with existing future earnings)")
            else:
                logger.info(f"💾 SKIP mode: Found {len(symbols_with_cache)} symbols with cache files out of {len(symbols_to_check)} that need updates")
            
            if symbols_with_cache:
                logger.info(f"💾 Will process symbols with cache: {symbols_with_cache[:10]}{'...' if len(symbols_with_cache) > 10 else ''}")
            else:
                logger.info("💾 No symbols have cache files - returning empty list")
            
            return symbols_with_cache
        
        # Check if force reload is enabled for other modes
        if self.force_reload:
            logger.info("🔄 Force reload enabled - will process ALL symbols (ignoring database state)")
            symbols_needing_updates = all_symbols
        else:
            # First apply smart filtering to get symbols that need updates
            symbols_needing_updates = self.smart_filter_symbols_for_updates(all_symbols, days_threshold=self.smart_filter_days)
        
        if self.download_mode == Constants.DOWNLOAD_MODE_DOWNLOAD:
            logger.info("🌐 Download mode: DOWNLOAD - will download fresh data for all symbols")
            return symbols_needing_updates
        
        # For AUTO mode, check cache freshness
        if self.download_mode == Constants.DOWNLOAD_MODE_AUTO:
            symbols_needing_download = []
            
            for symbol in symbols_needing_updates:
                if not self._should_use_cache_for_symbol(symbol):
                    symbols_needing_download.append(symbol)
            
            cache_fresh_count = len(symbols_needing_updates) - len(symbols_needing_download)
            if cache_fresh_count > 0:
                logger.info(f"📂 Auto mode: {cache_fresh_count} symbols have fresh cache, {len(symbols_needing_download)} need downloading")
            
            return symbols_needing_download
        
        return symbols_needing_updates

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
            
            # Get the symbols that were actually attempted for processing (from earlier in the workflow)
            attempted_symbols = getattr(self, '_attempted_symbols', [])
            skipped_symbols = getattr(self, '_skipped_symbols', [])
            
            logger.info(f"🔍 DIAGNOSTIC: save_earnings_data called with:")
            logger.info(f"   Max earnings count: {max_earnings_count}")
            logger.info(f"   History earnings count: {history_earnings_count}")
            logger.info(f"   Attempted symbols: {len(attempted_symbols)} symbols: {sorted(attempted_symbols)}")
            logger.info(f"   Skipped symbols: {len(skipped_symbols)} symbols: {sorted(skipped_symbols)}")
            
            if max_earnings_count == 0 and history_earnings_count == 0:
                logger.info("ℹ️ No earnings data to save - preserving existing data")
                logger.info("🔍 DIAGNOSTIC: This should preserve ALL existing records")
                return
            
            # Additional validation: check if DataFrames have actual symbols
            new_symbols = []
            if max_earnings_count > 0:
                symbols_in_main = [row.Symbol for row in max_earnings_df.select("Symbol").distinct().collect()]
                if not symbols_in_main:
                    logger.warning("⚠️ Max earnings DataFrame has records but no symbols - skipping main table update")
                    max_earnings_count = 0
                else:
                    new_symbols = symbols_in_main
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
                    
                    # Debug logging
                    existing_symbols = [row.Symbol for row in existing_main_df.select("Symbol").distinct().collect()]
                    existing_count = existing_main_df.count()
                    
                    logger.info(f"🔍 DEBUG: Existing table has {existing_count} records for {len(existing_symbols)} symbols")
                    logger.info(f"🔍 DEBUG: New data has {max_earnings_count} records for {len(new_symbols)} symbols")
                    logger.info(f"🔍 DEBUG: New symbols: {new_symbols}")
                    
                    # ENHANCED PRESERVATION LOGIC
                    # Check if force reload is enabled - if so, don't preserve symbols that have new data
                    if hasattr(self, 'force_reload') and self.force_reload:
                        logger.info("🔄 FORCE RELOAD mode: Will overwrite existing data for symbols being processed")
                        # Only preserve symbols that were NOT attempted for processing
                        symbols_to_preserve_definitely = [s for s in existing_symbols if s not in attempted_symbols]
                        symbols_attempted_but_no_data = [s for s in attempted_symbols if s not in new_symbols]
                        all_symbols_to_preserve = list(set(symbols_to_preserve_definitely + symbols_attempted_but_no_data))
                    else:
                        # Normal mode: Preserve ALL symbols that weren't attempted for processing, regardless of reason:
                        # 1. Symbols that exist in DB but were not attempted for processing
                        # 2. Symbols that were attempted but failed to provide data
                        
                        # Symbols that should definitely be preserved (not attempted at all)
                        symbols_to_preserve_definitely = [s for s in existing_symbols if s not in attempted_symbols]
                        
                        # Symbols that were attempted but didn't provide new data
                        symbols_attempted_but_no_data = [s for s in attempted_symbols if s not in new_symbols]
                        
                        # All symbols to preserve
                        all_symbols_to_preserve = list(set(symbols_to_preserve_definitely + symbols_attempted_but_no_data))
                    
                    logger.info(f"🔍 DEBUG: Enhanced preservation logic:")
                    logger.info(f"   Symbols not attempted (definitely preserve): {len(symbols_to_preserve_definitely)} symbols: {sorted(symbols_to_preserve_definitely)}")
                    logger.info(f"   Symbols attempted but no data: {len(symbols_attempted_but_no_data)} symbols: {sorted(symbols_attempted_but_no_data)}")
                    logger.info(f"   Total symbols to preserve: {len(all_symbols_to_preserve)} symbols: {sorted(all_symbols_to_preserve)}")
                    
                    # CRITICAL: When using --limit, we must preserve ALL symbols that weren't attempted
                    if hasattr(self, 'symbol_limit') and self.symbol_limit:
                        logger.info(f"🔢 LIMIT MODE: Ensuring preservation of ALL non-attempted symbols")
                        logger.info(f"   Attempted symbols: {len(attempted_symbols)} symbols: {sorted(attempted_symbols)}")
                        logger.info(f"   Must preserve: {len(symbols_to_preserve_definitely)} symbols: {sorted(symbols_to_preserve_definitely)}")
                    
                    # Keep existing records for symbols that should be preserved
                    existing_to_keep = existing_main_df.filter(col("Symbol").isin(all_symbols_to_preserve))
                    existing_to_keep_count = existing_to_keep.count()
                    
                    logger.info(f"🔍 DEBUG: Existing records to preserve: {existing_to_keep_count}")
                    
                    if existing_to_keep_count > 0:
                        preserved_symbols = [row.Symbol for row in existing_to_keep.select("Symbol").distinct().collect()]
                        logger.info(f"🔍 DEBUG: Actually preserved symbols: {sorted(preserved_symbols)}")
                        
                        # Validation: check if we're missing any symbols we should preserve
                        missing_symbols = [s for s in all_symbols_to_preserve if s not in preserved_symbols]
                        if missing_symbols:
                            logger.warning(f"⚠️ DEBUG: Expected to preserve but missing: {sorted(missing_symbols)}")
                    
                    # CRITICAL FIX: Ensure both DataFrames have the same schema before union
                    # The existing data from DB might have different date types than processed data
                    logger.info("🔧 SCHEMA FIX: Ensuring schema compatibility between existing and new data")
                    
                    # Make sure existing data has the same date format as new data
                    existing_to_keep_normalized = existing_to_keep.withColumn(
                        "earnings_date", to_date(col("earnings_date"))
                    )
                    
                    # Ensure new data also has date format (should already be done but double-check)
                    max_earnings_df_normalized = max_earnings_df.withColumn(
                        "earnings_date", to_date(col("earnings_date"))
                    )
                    
                    # Debug: Check schemas before union
                    logger.info(f"🔍 SCHEMA DEBUG: Existing data schema: {existing_to_keep_normalized.schema}")
                    logger.info(f"🔍 SCHEMA DEBUG: New data schema: {max_earnings_df_normalized.schema}")
                    
                    # Combine preserved existing data with new data using normalized schemas
                    combined_df = existing_to_keep_normalized.union(max_earnings_df_normalized)
                    
                    # CRITICAL: Immediate validation after union to catch data loss
                    immediate_count = combined_df.count()
                    immediate_symbols = [row.Symbol for row in combined_df.select("Symbol").distinct().collect()]
                    
                    logger.info(f"🔍 IMMEDIATE UNION CHECK: {immediate_count} records for {len(immediate_symbols)} symbols")
                    logger.info(f"🔍 IMMEDIATE UNION CHECK: Symbols: {sorted(immediate_symbols)}")
                    
                    # Check if union lost data immediately
                    expected_union_count = existing_to_keep_count + max_earnings_count
                    expected_union_symbols = len(all_symbols_to_preserve) + len(new_symbols)
                    
                    if immediate_count != expected_union_count:
                        logger.error(f"🚨 UNION DATA LOSS: Expected {expected_union_count}, got {immediate_count}")
                        logger.error(f"🚨 UNION DATA LOSS: Lost {expected_union_count - immediate_count} records in union operation")
                        
                        # Recovery: Try to rebuild the union
                        logger.info("🔧 UNION RECOVERY: Attempting to rebuild union operation")
                        try:
                            # Force materialization of both DataFrames before union
                            existing_to_keep_normalized = existing_to_keep_normalized.cache()
                            max_earnings_df_normalized = max_earnings_df_normalized.cache()
                            
                            # Rebuild union
                            combined_df = existing_to_keep_normalized.union(max_earnings_df_normalized)
                            
                            # Re-check
                            recovery_count = combined_df.count()
                            logger.info(f"🔧 UNION RECOVERY: After rebuild: {recovery_count} records")
                            
                        except Exception as union_error:
                            logger.error(f"🚨 UNION RECOVERY FAILED: {union_error}")
                    
                    if len(immediate_symbols) != expected_union_symbols:
                        logger.error(f"🚨 UNION SYMBOL LOSS: Expected {expected_union_symbols} symbols, got {len(immediate_symbols)}")
                        
                        # Find missing symbols
                        expected_all_symbols = set(all_symbols_to_preserve + new_symbols)
                        actual_symbols = set(immediate_symbols)
                        missing_in_union = expected_all_symbols - actual_symbols
                        
                        if missing_in_union:
                            logger.error(f"🚨 UNION MISSING SYMBOLS: {sorted(missing_in_union)}")
                            
                            # Recovery: Try to add missing symbols back
                            logger.info("🔧 SYMBOL RECOVERY: Attempting to add missing symbols back")
                            try:
                                for missing_symbol in missing_in_union:
                                    # Check if missing symbol was in preserved data
                                    if missing_symbol in all_symbols_to_preserve:
                                        missing_data = existing_main_df.filter(col("Symbol") == missing_symbol)
                                        missing_data_normalized = missing_data.withColumn("earnings_date", to_date(col("earnings_date")))
                                        combined_df = combined_df.union(missing_data_normalized)
                                        logger.info(f"🔧 SYMBOL RECOVERY: Added back {missing_symbol}")
                                    
                                    # Check if missing symbol was in new data
                                    if missing_symbol in new_symbols:
                                        missing_new_data = max_earnings_df.filter(col("Symbol") == missing_symbol)
                                        missing_new_data_normalized = missing_new_data.withColumn("earnings_date", to_date(col("earnings_date")))
                                        combined_df = combined_df.union(missing_new_data_normalized)
                                        logger.info(f"🔧 SYMBOL RECOVERY: Added back {missing_symbol}")
                                        
                            except Exception as symbol_recovery_error:
                                logger.error(f"🚨 SYMBOL RECOVERY FAILED: {symbol_recovery_error}")
                    
                    # Final validation before save
                    final_count = combined_df.count()
                    final_symbols = [row.Symbol for row in combined_df.select("Symbol").distinct().collect()]
                    
                    logger.info(f"🔍 DEBUG: Final combined data: {final_count} records for {len(final_symbols)} symbols")
                    logger.info(f"🔍 DEBUG: Final symbols: {sorted(final_symbols)}")
                    
                    # Critical validation: ensure we're not losing data
                    if final_count < existing_count:
                        logger.error(f"🚨 CRITICAL: Final count {final_count} is less than existing count {existing_count}")
                        logger.error(f"🚨 CRITICAL: This would DROP {existing_count - final_count} records!")
                        logger.error(f"🚨 CRITICAL: Existing symbols: {sorted(existing_symbols)}")
                        logger.error(f"🚨 CRITICAL: Final symbols: {sorted(final_symbols)}")
                        
                        # Find which symbols would be lost
                        lost_symbols = [s for s in existing_symbols if s not in final_symbols]
                        if lost_symbols:
                            logger.error(f"🚨 CRITICAL: Symbols that would be LOST: {sorted(lost_symbols)}")
                            
                            # Recover the lost symbols
                            logger.info("🔧 RECOVERY: Adding back lost symbols to prevent data loss")
                            lost_symbols_df = existing_main_df.filter(col("Symbol").isin(lost_symbols))
                            combined_df = combined_df.union(lost_symbols_df)
                            
                            # Re-validate
                            final_count = combined_df.count()
                            final_symbols = [row.Symbol for row in combined_df.select("Symbol").distinct().collect()]
                            logger.info(f"🔧 RECOVERY: New final count: {final_count} records for {len(final_symbols)} symbols")
                    
                    # Additional validation: check symbol count
                    if len(final_symbols) < len(existing_symbols):
                        logger.error(f"🚨 CRITICAL: Symbol count decreased from {len(existing_symbols)} to {len(final_symbols)}")
                        lost_symbols = [s for s in existing_symbols if s not in final_symbols]
                        logger.error(f"🚨 CRITICAL: Lost symbols: {sorted(lost_symbols)}")
                        
                        # Force recovery
                        logger.info("🔧 EMERGENCY RECOVERY: Force adding all lost symbols")
                        for lost_symbol in lost_symbols:
                            lost_symbol_df = existing_main_df.filter(col("Symbol") == lost_symbol)
                            combined_df = combined_df.union(lost_symbol_df)
                        
                        # Re-validate
                        final_count = combined_df.count()
                        final_symbols = [row.Symbol for row in combined_df.select("Symbol").distinct().collect()]
                        logger.info(f"🔧 EMERGENCY RECOVERY: Final count: {final_count} records for {len(final_symbols)} symbols")
                    
                    # ADDITIONAL VALIDATION: When using --limit, ensure we NEVER lose existing data
                    if hasattr(self, 'symbol_limit') and self.symbol_limit:
                        symbols_that_should_be_preserved = [s for s in existing_symbols if s not in attempted_symbols]
                        preserved_symbols = [s for s in existing_symbols if s in final_symbols]
                        
                        missing_preserved = [s for s in symbols_that_should_be_preserved if s not in preserved_symbols]
                        if missing_preserved:
                            logger.error(f"🚨 LIMIT MODE CRITICAL: Missing preserved symbols: {sorted(missing_preserved)}")
                            logger.info("🔧 EMERGENCY RECOVERY: Force adding missing preserved symbols")
                            
                            missing_df = existing_main_df.filter(col("Symbol").isin(missing_preserved))
                            combined_df = combined_df.union(missing_df)
                            
                            # Re-validate
                            final_count = combined_df.count()
                            final_symbols = [row.Symbol for row in combined_df.select("Symbol").distinct().collect()]
                            logger.info(f"🔧 LIMIT MODE RECOVERY: Final count: {final_count} records for {len(final_symbols)} symbols")
                        else:
                            logger.info(f"✅ LIMIT MODE: All {len(symbols_that_should_be_preserved)} non-attempted symbols properly preserved")
                    
                    if final_count > 0:
                        # FINAL CRITICAL CHECK: Validate data right before database write
                        pre_write_count = combined_df.count()
                        pre_write_symbols = [row.Symbol for row in combined_df.select("Symbol").distinct().collect()]
                        
                        logger.info(f"🔍 PRE-WRITE VALIDATION: {pre_write_count} records for {len(pre_write_symbols)} symbols")
                        logger.info(f"🔍 PRE-WRITE SYMBOLS: {sorted(pre_write_symbols)}")
                        
                        # Final validation against expected data
                        expected_final_symbols = set(all_symbols_to_preserve + new_symbols)
                        actual_final_symbols = set(pre_write_symbols)
                        
                        if actual_final_symbols != expected_final_symbols:
                            logger.error(f"🚨 PRE-WRITE VALIDATION FAILED:")
                            logger.error(f"   Expected: {sorted(expected_final_symbols)}")
                            logger.error(f"   Actual: {sorted(actual_final_symbols)}")
                            
                            missing_final = expected_final_symbols - actual_final_symbols
                            extra_final = actual_final_symbols - expected_final_symbols
                            
                            if missing_final:
                                logger.error(f"🚨 MISSING BEFORE WRITE: {sorted(missing_final)}")
                            if extra_final:
                                logger.error(f"🚨 EXTRA BEFORE WRITE: {sorted(extra_final)}")
                        else:
                            logger.info("✅ PRE-WRITE VALIDATION: All expected symbols present")
                        
                        # Show what's being saved
                        logger.info("📊 Final earnings data being saved:")
                        preview_count = min(final_count, 20)
                        combined_df.select("Symbol", "earnings_date", "earnings_high", "earnings_low", "earnings_average").show(preview_count, truncate=False)
                        
                        # Force materialization before write to prevent lazy evaluation issues
                        combined_df = combined_df.cache()
                        materialized_count = combined_df.count()  # Force materialization
                        
                        if materialized_count != pre_write_count:
                            logger.error(f"🚨 MATERIALIZATION LOSS: {pre_write_count} → {materialized_count}")
                        
                        logger.info(f"💾 WRITING TO DATABASE: {materialized_count} records")
                        
                        combined_df.write.format('jdbc').options(
                            url=self.url,
                            driver=self.sql_driver,
                            dbtable="stocks_earnings",
                            user=self.username,
                            password=self.password
                        ).mode('overwrite').save()
                        
                        logger.info(f"✅ Updated {max_earnings_count} symbols, preserved {existing_to_keep_count} existing records")
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
            else:
                logger.info("ℹ️ No new earnings data to save - preserving all existing records")
                logger.info("🔍 DIAGNOSTIC: Main table should remain unchanged")
                
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
            logger.info("🚀 Starting Stock Data Loader with Multi-Threading and File Caching...")
            logger.info(f"💾 Download Mode: {self.download_mode}")
            logger.info(f"📂 Cache Max Age: {self.cache_max_age_days} days")
            
            # Show current cache statistics
            cache_stats = FileManager.get_cache_stats()
            logger.info(f"📊 Current Cache Statistics:")
            logger.info(f"   Total cached files: {cache_stats.get('total_files', 0)}")
            logger.info(f"   Earnings: {cache_stats.get('earnings', 0)} files")
            logger.info(f"   Company Info: {cache_stats.get('company_info', 0)} files")
            logger.info(f"   Sustainability: {cache_stats.get('sustainability', 0)} files")
            logger.info(f"   Quarterly Financials: {cache_stats.get('quarterly_financials', 0)} files")
            
            # DIAGNOSTIC: Check initial table state
            try:
                logger.info("🔍 DIAGNOSTIC: Checking initial table state...")
                initial_table_df = self.spark.read.format("jdbc").options(
                    url=self.url,
                    driver=self.sql_driver,
                    dbtable=self.earnings_table,
                    user=self.username,
                    password=self.password
                ).load()
                
                initial_count = initial_table_df.count()
                initial_symbols = [row.Symbol for row in initial_table_df.select("Symbol").distinct().collect()]
                
                logger.info(f"🔍 DIAGNOSTIC: Initial table has {initial_count} records for {len(initial_symbols)} symbols")
                logger.info(f"🔍 DIAGNOSTIC: Initial symbols: {sorted(initial_symbols)}")
                
                # Check dates in existing data
                initial_table_df = initial_table_df.withColumn("earnings_date", to_date(col("earnings_date")))
                from datetime import date
                today = date.today()
                
                future_earnings = initial_table_df.filter(
                    col("earnings_date") > lit(today.strftime('%Y-%m-%d'))
                ).select("Symbol").distinct().collect()
                
                future_symbols = [row.Symbol for row in future_earnings]
                logger.info(f"🔍 DIAGNOSTIC: Symbols with future earnings: {len(future_symbols)} symbols: {sorted(future_symbols)}")
                
                past_earnings = initial_table_df.filter(
                    col("earnings_date") <= lit(today.strftime('%Y-%m-%d'))
                ).select("Symbol").distinct().collect()
                
                past_symbols = [row.Symbol for row in past_earnings]
                logger.info(f"🔍 DIAGNOSTIC: Symbols with past/today earnings: {len(past_symbols)} symbols: {sorted(past_symbols)}")
                
            except Exception as diag_error:
                logger.warning(f"🔍 DIAGNOSTIC: Could not check initial table state: {diag_error}")
                initial_count = 0
                initial_symbols = []
            
            # Get symbols to process
            if self.single_symbol:
                # Single symbol mode - first get all available symbols to validate
                available_symbols = self.get_stock_symbols()
                if not available_symbols:
                    logger.error("No symbols available from config/database. Exiting.")
                    return
                
                # Validate that the specified symbol exists in available symbols
                if self.single_symbol.upper() not in available_symbols:
                    logger.error(f"❌ Single symbol {self.single_symbol.upper()} not found in available symbols")
                    logger.error(f"Available symbols: {sorted(available_symbols)}")
                    return
                
                # Process only the specified symbol
                all_symbols = [self.single_symbol.upper()]
                logger.info(f"🎯 Single Symbol Mode: Processing only {self.single_symbol.upper()}")
                logger.info(f"⚠️  Note: All other symbols will be preserved in database (no changes)")
                logger.info(f"🔍 Available symbols: {len(available_symbols)} total, processing 1 specified symbol")
            else:
                all_symbols = self.get_stock_symbols()
                if not all_symbols:
                    logger.error("No symbols to process. Exiting.")
                    return
                logger.info(f"🔍 DIAGNOSTIC: Input symbols from config/DB: {len(all_symbols)} symbols: {sorted(all_symbols)}")
            
            # Enhanced filtering: Consider download mode, cache freshness, and future earnings
            symbols = self.get_symbols_needing_download(all_symbols)
            
            # Track symbols before applying limit (for proper database preservation)
            symbols_before_limit = symbols.copy()
            
            # Apply symbol limit if specified
            if hasattr(self, 'symbol_limit') and self.symbol_limit and len(symbols) > self.symbol_limit:
                original_count = len(symbols)
                limited_symbols = symbols[:self.symbol_limit]
                skipped_by_limit = symbols[self.symbol_limit:]
                symbols = limited_symbols
                logger.info(f"🔢 Applied symbol limit: {original_count} → {len(symbols)} symbols (limited to {self.symbol_limit})")
                logger.info(f"🎯 Processing limited set: {symbols}")
                logger.info(f"⏭️ Skipped by limit: {len(skipped_by_limit)} symbols: {skipped_by_limit}")
                logger.info(f"💡 Tip: Use --limit for testing, remove it to process all symbols")
            else:
                skipped_by_limit = []
            
            # DIAGNOSTIC: Check what enhanced filtering did
            skipped_by_filtering = [s for s in all_symbols if s not in symbols_before_limit]
            logger.info(f"🔍 DIAGNOSTIC: Enhanced filtering results:")
            logger.info(f"   Input: {len(all_symbols)} symbols")
            logger.info(f"   To process: {len(symbols)} symbols: {sorted(symbols) if symbols else []}")
            logger.info(f"   Skipped by filtering: {len(skipped_by_filtering)} symbols: {sorted(skipped_by_filtering)}")
            logger.info(f"   Skipped by limit: {len(skipped_by_limit)} symbols: {sorted(skipped_by_limit)}")
            
            # Store symbol tracking for use in save methods
            # CRITICAL: Only symbols that were actually attempted for processing
            self._attempted_symbols = symbols.copy() if symbols else []
            # CRITICAL: All symbols that should be preserved = skipped by filtering + skipped by limit  
            self._skipped_symbols = skipped_by_filtering + skipped_by_limit
            
            logger.info(f"🔍 DIAGNOSTIC: Set tracking attributes:")
            logger.info(f"   _attempted_symbols: {len(self._attempted_symbols)} symbols")
            logger.info(f"   _skipped_symbols: {len(self._skipped_symbols)} symbols")
            
            if not symbols:
                logger.info("🎉 All symbols are up to date or using cache! No processing needed.")
                logger.info("🔍 DIAGNOSTIC: No processing = no database changes expected")
                return
            
            # DIAGNOSTIC: Predict what should happen to the database
            symbols_that_should_be_preserved = [s for s in initial_symbols if s not in symbols]
            symbols_that_might_be_updated = [s for s in symbols if s in initial_symbols]
            symbols_that_are_new = [s for s in symbols if s not in initial_symbols]
            
            logger.info(f"🔍 DIAGNOSTIC: Database change prediction:")
            logger.info(f"   Should be preserved (not processed): {len(symbols_that_should_be_preserved)} symbols: {sorted(symbols_that_should_be_preserved)}")
            logger.info(f"   Might be updated (processed, existing): {len(symbols_that_might_be_updated)} symbols: {sorted(symbols_that_might_be_updated)}")
            logger.info(f"   New additions (processed, new): {len(symbols_that_are_new)} symbols: {sorted(symbols_that_are_new)}")
            
            expected_min_final_count = len(symbols_that_should_be_preserved)
            logger.info(f"🔍 DIAGNOSTIC: Expected minimum final count: {expected_min_final_count} (preserved records)")
            logger.info(f"🔍 DIAGNOSTIC: Expected maximum final count: {len(initial_symbols) + len(symbols_that_are_new)} (if all updates succeed)")
            
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
            logger.info(f"✅ Successfully processed: {len(successful_symbols)} symbols: {successful_symbols}")
            if failed_symbols:
                logger.warning(f"⚠️ Failed to process: {len(failed_symbols)} symbols: {failed_symbols}")
            
            # Additional symbol tracking for database operations
            skipped_symbols = [s for s in all_symbols if s not in symbols]
            if skipped_symbols:
                logger.info(f"⏭️ Skipped by smart filtering: {len(skipped_symbols)} symbols: {skipped_symbols}")
            
            logger.info(f"📊 Complete Symbol Summary:")
            logger.info(f"   Total input symbols: {len(all_symbols)}")
            logger.info(f"   Smart filtered (skipped): {len(skipped_symbols)}")
            logger.info(f"   Attempted processing: {len(symbols)}")
            logger.info(f"   Successfully processed: {len(successful_symbols)}")
            logger.info(f"   Failed processing: {len(failed_symbols)}")
            
            # DIAGNOSTIC: Check what actually got collected
            symbols_with_earnings_data = []
            if self.all_earnings_data:
                symbols_with_earnings_data = list(set([d['Symbol'] for d in self.all_earnings_data]))
            
            logger.info(f"🔍 DIAGNOSTIC: Symbols that successfully provided earnings data: {len(symbols_with_earnings_data)} symbols: {sorted(symbols_with_earnings_data)}")
            
            # Process the collected data
            logger.info("🔄 Processing collected data...")
            
            earnings_df = self.process_earnings_data(self.all_earnings_data)
            quarterly_df = self.process_quarterly_data(self.all_quarterly_data)
            company_info_df = self.process_company_info_data(self.all_company_info_data)
            sustainability_df = self.process_sustainability_data(self.all_sustainability_data)
            quarterly_financials_df = self.process_quarterly_financials_data(self.all_quarterly_financials_data)
            
            # Log what data we have before converting to Spark DataFrames
            logger.info(f"📊 Data Summary Before Database Operations:")
            logger.info(f"   Earnings data: {len(self.all_earnings_data)} records")
            logger.info(f"   Quarterly data: {len(self.all_quarterly_data)} records")
            logger.info(f"   Company info data: {len(self.all_company_info_data)} records")
            logger.info(f"   Sustainability data: {len(self.all_sustainability_data)} records")
            logger.info(f"   Quarterly financials data: {len(self.all_quarterly_financials_data)} records")
            
            # Get unique symbols from each data type for tracking
            if self.all_earnings_data:
                earnings_symbols = list(set([d['Symbol'] for d in self.all_earnings_data]))
                logger.info(f"   Earnings symbols: {earnings_symbols}")
            
            if self.all_company_info_data:
                company_info_symbols = list(set([d['Symbol'] for d in self.all_company_info_data]))
                logger.info(f"   Company info symbols: {company_info_symbols}")
            
            # Convert to Spark DataFrames
            earnings_spark_df, quarterly_spark_df, company_info_spark_df, sustainability_spark_df, quarterly_financials_spark_df = self.create_spark_dataframes(
                earnings_df, quarterly_df, company_info_df, sustainability_df, quarterly_financials_df)
            
            # Process and save earnings data
            if earnings_spark_df.count() > 0:
                logger.info("🔍 DIAGNOSTIC: About to process earnings data for database save...")
                max_earnings_df, earnings_history_df = self.filter_earnings_data(earnings_spark_df)
                self.save_earnings_data(max_earnings_df, earnings_history_df)
            else:
                logger.info("ℹ️ No earnings data to process - this should preserve all existing records")
                logger.info("🔍 DIAGNOSTIC: No earnings data = no database changes expected")
            
            # DIAGNOSTIC: Check final table state
            try:
                logger.info("🔍 DIAGNOSTIC: Checking final table state...")
                final_table_df = self.spark.read.format("jdbc").options(
                    url=self.url,
                    driver=self.sql_driver,
                    dbtable=self.earnings_table,
                    user=self.username,
                    password=self.password
                ).load()
                
                final_count = final_table_df.count()
                final_symbols = [row.Symbol for row in final_table_df.select("Symbol").distinct().collect()]
                
                logger.info(f"🔍 DIAGNOSTIC: Final table has {final_count} records for {len(final_symbols)} symbols")
                logger.info(f"🔍 DIAGNOSTIC: Final symbols: {sorted(final_symbols)}")
                
                # Compare with initial state
                logger.info(f"🔍 DIAGNOSTIC: Record count change: {initial_count} → {final_count} (Δ{final_count - initial_count})")
                
                # Find lost and gained symbols
                lost_symbols = [s for s in initial_symbols if s not in final_symbols]
                gained_symbols = [s for s in final_symbols if s not in initial_symbols]
                
                if lost_symbols:
                    logger.error(f"🚨 DIAGNOSTIC: LOST SYMBOLS: {len(lost_symbols)} symbols: {sorted(lost_symbols)}")
                    
                    # Check if lost symbols were in the processing list
                    lost_symbols_that_were_processed = [s for s in lost_symbols if s in symbols]
                    lost_symbols_that_were_skipped = [s for s in lost_symbols if s not in symbols]
                    
                    if lost_symbols_that_were_processed:
                        logger.error(f"🚨 DIAGNOSTIC: Lost symbols that WERE processed: {sorted(lost_symbols_that_were_processed)}")
                    if lost_symbols_that_were_skipped:
                        logger.error(f"🚨 DIAGNOSTIC: Lost symbols that were SKIPPED (this should NOT happen): {sorted(lost_symbols_that_were_skipped)}")
                
                if gained_symbols:
                    logger.info(f"✅ DIAGNOSTIC: GAINED SYMBOLS: {len(gained_symbols)} symbols: {sorted(gained_symbols)}")
                
                # Critical error if we lost more records than expected
                if final_count < expected_min_final_count:
                    logger.error(f"🚨 CRITICAL DIAGNOSTIC ERROR:")
                    logger.error(f"   Expected minimum: {expected_min_final_count}")
                    logger.error(f"   Actual final: {final_count}")
                    logger.error(f"   UNEXPLAINED LOSS: {expected_min_final_count - final_count} records")
                    
            except Exception as final_diag_error:
                logger.error(f"🔍 DIAGNOSTIC: Could not check final table state: {final_diag_error}")
            
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
        python stock_earning_loader.py [--download-mode {download,skip,auto}] [--cache-max-age-days N] [--limit N] [--symbol SYMBOL] [--force-reload]
        
    Examples:
        python stock_earning_loader.py --download-mode download  # Force download from APIs
        python stock_earning_loader.py --download-mode skip     # Only use cached data
        python stock_earning_loader.py --download-mode auto     # Use cache if fresh, otherwise download
        python stock_earning_loader.py --download-mode download --limit 10  # Download only 10 symbols
        python stock_earning_loader.py --symbol AAPL           # Process only AAPL symbol
        python stock_earning_loader.py --symbol GOOGL --download-mode download  # Force download for GOOGL only
        python stock_earning_loader.py --download-mode skip --force-reload  # Reload ALL cached symbols (ignore database state)
        python stock_earning_loader.py --force-reload          # Force reload mode (updates existing + adds new records)
    """
    parser = argparse.ArgumentParser(description='Stock Data Loader with File Caching')
    parser.add_argument('--download-mode', 
                       choices=['download', 'skip', 'auto'], 
                       default=None,
                       help='Download mode: download (always download from APIs), skip (only use cache), auto (use cache if fresh)')
    parser.add_argument('--cache-max-age-days', 
                       type=int, 
                       default=None,
                       help='Maximum age of cache files in days')
    parser.add_argument('--limit', 
                       type=int, 
                       default=None,
                       help='Maximum number of symbols to process (useful for testing)')
    parser.add_argument('--symbol', 
                       type=str, 
                       default=None,
                       help='Process only this specific symbol (e.g., AAPL, GOOGL)')
    parser.add_argument('--force-reload', 
                       action='store_true',
                       help='Force reload ALL cached symbols, ignoring database state and updating existing records')
    
    args = parser.parse_args()
    
    # Validate argument combinations
    if args.symbol and args.limit:
        logger.error("❌ Cannot use --symbol and --limit together. Use --symbol for single symbol or --limit for multiple symbols.")
        sys.exit(1)
    
    try:
        loader = StockDataLoader()
        
        # Override config with command-line arguments
        if args.download_mode:
            loader.download_mode = args.download_mode
            logger.info(f"🔧 Command-line override: download_mode = {args.download_mode}")
        
        if args.cache_max_age_days:
            loader.cache_max_age_days = args.cache_max_age_days
            logger.info(f"🔧 Command-line override: cache_max_age_days = {args.cache_max_age_days}")
        
        if args.limit:
            loader.symbol_limit = args.limit
            logger.info(f"🔧 Command-line override: symbol_limit = {args.limit}")
        else:
            loader.symbol_limit = None
        
        if args.symbol:
            loader.single_symbol = args.symbol.upper()
            logger.info(f"🔧 Command-line override: single_symbol = {args.symbol.upper()}")
        else:
            loader.single_symbol = None
        
        if args.force_reload:
            loader.force_reload = True
            logger.info(f"🔧 Command-line override: force_reload = True")
        else:
            loader.force_reload = False
        
        # Display final configuration
        logger.info("🎯 Final Configuration:")
        logger.info(f"   Download Mode: {loader.download_mode}")
        logger.info(f"   Cache Max Age: {loader.cache_max_age_days} days")
        if loader.force_reload:
            logger.info(f"   Force Reload: {loader.force_reload}")
            logger.warning("🔄 FORCE RELOAD MODE: --force-reload parameter is active")
            logger.warning("🔄 This will process ALL symbols with cache files, ignoring database state")
            logger.warning("🔄 Existing records for processed symbols will be UPDATED/OVERWRITTEN")
            logger.warning("🔄 Use this after truncating tables or when you want to refresh all cached data")
        if loader.single_symbol:
            logger.info(f"   Single Symbol: {loader.single_symbol}")
            logger.warning("🎯 SINGLE SYMBOL MODE: --symbol parameter is active")
            logger.warning("🎯 This will only process the specified symbol but preserve ALL other existing data")
            logger.warning("🎯 Remove --symbol for production runs to process all symbols")
        elif loader.symbol_limit:
            logger.info(f"   Symbol Limit: {loader.symbol_limit} symbols")
            logger.warning("⚠️  TESTING MODE: --limit parameter is active")
            logger.warning("⚠️  This will only process the first N symbols but preserve ALL existing data")
            logger.warning("⚠️  Remove --limit for production runs to process all symbols")
        
        # Run the loader
        loader.run()
        
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()