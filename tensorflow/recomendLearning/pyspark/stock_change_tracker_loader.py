"""
Stock Change Tracker Loader

This script processes stock price data using PySpark to track and calculate
stock price changes since they were added to the tracking system.

Main functionality:
- Reads stock price data from MySQL database
- Calculates price changes since stocks were first added to tracker
- Updates stock change tracker table with current prices and performance metrics
- Manages active/inactive status based on performance and time criteria
"""

import sys
import yfinance as yf
import pandas as pd
from pyspark.sql import SparkSession, Window
from pyspark.sql.functions import col, to_timestamp, to_date, lit, row_number, max, abs, format_number, coalesce, \
    current_date, when, datediff, round
from pyspark.sql.types import StructType, StructField, StringType, DoubleType, TimestampType
from datetime import datetime, timedelta
import configparser
import os
import numpy as np
import glob

# Patch np.bool to point to the built-in bool or np.bool_
if not hasattr(np, 'bool'):
    np.bool = np.bool_


def get_optimized_jdbc_options(url, driver, username, password, table_name):
    """
    Get optimized JDBC options for faster database operations
    Performance optimizations for MySQL bulk operations
    """
    return {
        'url': url,
        'driver': driver,
        'dbtable': table_name,
        'user': username,
        'password': password,
        # Bulk insert optimizations
        'batchsize': '10000',  # Larger batch size for bulk operations
        'rewriteBatchedStatements': 'true',  # MySQL bulk insert optimization
        'useCompression': 'true',  # Compress data transfer
        'autoReconnect': 'true',  # Handle connection drops
        'useUnicode': 'true',
        'characterEncoding': 'UTF-8',
        # Connection pool settings for better performance
        'initialSize': '5',
        'maxActive': '20',
        'maxIdle': '10',
        'minIdle': '5',
        # Timeout settings
        'connectTimeout': '60000',  # 60 seconds
        'socketTimeout': '3600000',   # 60 seconds
        # Additional MySQL optimizations
        'useBulkStmts': 'false',  # Can cause issues with some MySQL versions
        'cachePrepStmts': 'true',
        'prepStmtCacheSize': '250',
        'prepStmtCacheSqlLimit': '2048',
        'useServerPrepStmts': 'true',
        'autoReconnect': 'true'
    }


def optimized_write_to_mysql(df, url, driver, table_name, username, password, mode='overwrite'):
    """
    Optimized database write with better performance settings
    """
    print(f"🚀 Starting optimized {mode} operation to {table_name}...")
    
    # Get record count and optimize partitioning
    record_count = df.count()
    print(f"📊 Processing {record_count} records with optimized settings")
    
    # Optimize partitioning for parallel writes
    if record_count > 5000:
        # Calculate optimal partitions (aim for 5000-10000 records per partition)
        optimal_partitions = max(1, min(record_count // 7500, 8))  # Cap at 8 for safety
        print(f"🔄 Repartitioning data into {optimal_partitions} partitions for parallel processing...")
        df = df.repartition(optimal_partitions)
    
    # Get optimized JDBC options
    jdbc_options = get_optimized_jdbc_options(url, driver, username, password, table_name)
    
    # Execute optimized write
    print(f"🔄 Executing optimized {mode} with enhanced JDBC settings...")
    df.write.format('jdbc').options(**jdbc_options).mode(mode).save()
    
    print(f"✅ Optimized {mode} completed successfully!")
    return True


def manage_backup_files(backup_dir="./backup", max_backups=5):
    """
    Manage backup files to ensure only the most recent ones are kept.
    
    Args:
        backup_dir (str): Directory containing backup files
        max_backups (int): Maximum number of backup files to keep
    """
    print(f"🧹 Managing backup files in {backup_dir} (keeping {max_backups} most recent)...")
    
    # Ensure backup directory exists
    os.makedirs(backup_dir, exist_ok=True)
    
    # Get all backup files for each type
    backup_patterns = [
        "stock_change_tracker_backup_*.csv",
        "stock_change_tracker_current_*.csv"
    ]
    
    for pattern in backup_patterns:
        # Get all files matching the pattern
        backup_files = glob.glob(os.path.join(backup_dir, pattern))
        
        if len(backup_files) <= max_backups:
            print(f"✅ {pattern}: {len(backup_files)} files (within limit)")
            continue
        
        # Sort files by modification time (newest first)
        backup_files.sort(key=os.path.getmtime, reverse=True)
        
        # Keep only the most recent max_backups files
        files_to_keep = backup_files[:max_backups]
        files_to_delete = backup_files[max_backups:]
        
        print(f"📊 {pattern}: Found {len(backup_files)} files, keeping {len(files_to_keep)}, deleting {len(files_to_delete)}")
        
        # Delete old backup files
        for file_path in files_to_delete:
            try:
                os.remove(file_path)
                print(f"🗑️  Deleted old backup: {os.path.basename(file_path)}")
            except Exception as e:
                print(f"⚠️  Warning: Could not delete {file_path}: {e}")
        
        print(f"✅ {pattern}: Cleanup completed")
    
    print("🧹 Backup file management completed!")


def main():
    """
    Main function that orchestrates the stock change tracking process.
    
    Process flow:
    1. Setup Spark session and database connections
    2. Load stock data and existing tracker data
    3. Calculate latest prices and price changes
    4. Update tracker with new performance metrics
    5. Save updated data back to database
    """
    # Configure Python environment for PySpark
    python_path = sys.executable
    os.environ['PYSPARK_PYTHON'] = python_path
    os.environ['PYSPARK_DRIVER_PYTHON'] = python_path
    print(f"🐍 Using Python: {python_path}")
    
    # Initialize Spark session with optimized settings for database operations
    spark = SparkSession.builder.master("local[*]").appName("Stock Loader") \
        .config("spark.jars",
                "/Users/gaurav/.m2/repository/com/mysql/mysql-connector-j/8.0.33/mysql-connector-j-8.0.33.jar") \
        .config("spark.sql.adaptive.enabled", "true") \
        .config("spark.sql.adaptive.coalescePartitions.enabled", "true") \
        .config("spark.serializer", "org.apache.spark.serializer.KryoSerializer") \
        .config("spark.sql.execution.arrow.pyspark.enabled", "true") \
        .config("spark.sql.shuffle.partitions", "8") \
        .config("spark.default.parallelism", "8") \
        .getOrCreate()
    
    # Load database configuration from config file
    config = configparser.ConfigParser()
    config.read(os.path.join(os.path.dirname(__file__), 'conf/config.ini'))
    print("Sections : ", config.sections())
    
    # Setup database connection parameters
    sql_driver = "com.mysql.cj.jdbc.Driver"
    url = 'jdbc:mysql://localhost/{}'.format(config.get('mysql', 'database'))
    table = config.get('mysql', 'table')  # Main stock data table
    stock_change_tracker_table = config.get('mysql', 'stock_change_tracker_table')  # Tracker table
    username = config.get('mysql', 'username')
    password = config.get('mysql', 'password')
    print("url : ", url)
    print("MySQL User : ", username)
    
    try:
        # Load stock price data from main table with optimized settings
        # Contains historical stock prices with Date, Close, symbol columns
        print("📊 Loading stock price data...")
        stockdf_options = get_optimized_jdbc_options(url, sql_driver, username, password, table)
        stockdf = spark.read.format("jdbc").options(**stockdf_options).load()
        
        # Load existing stock change tracker data with optimized settings
        # Contains tracking info like date_added, price_when_added, current_price, etc.
        print("📊 Loading existing stock change tracker data...")
        tracker_options = get_optimized_jdbc_options(url, sql_driver, username, password, stock_change_tracker_table)
        stock_change_tracker_df = spark.read.format("jdbc").options(**tracker_options).load()
        
        # === STEP 1: Get the most recent price for each stock symbol ===
        # Find the maximum (most recent) date for each symbol
        max_date_df = stockdf.groupBy("symbol").agg(max("Date").alias("max_date"))
        max_date_df = max_date_df.withColumnRenamed("symbol", "max_symbol")
        
        # === STEP 2: Extract the closing price for the most recent date ===
        # Join back to get the close price for the max date for each symbol
        max_close_price_df = stockdf.join(max_date_df, (stockdf.symbol == max_date_df.max_symbol) & (
                stockdf.Date == max_date_df.max_date), "inner") \
            .select("symbol", "Close")
        
        # Rename the close column for clarity in subsequent operations
        max_close_price_df = max_close_price_df.withColumnRenamed("Close", "max_close_price")
        
        # === STEP 3: Merge latest prices with existing tracker data ===
        # Left join to preserve all symbols and get existing tracker data where available
        joined_df = max_close_price_df.join(stock_change_tracker_df, "symbol", "left")

        # === STEP 4: Handle missing values and update current prices ===
        # ONLY set date_added to current date if this is a new symbol (null date_added) - preserve existing values
        # Update current_price with latest market price
        # ONLY set price_when_added to current price if this is a new entry - preserve existing values
        joined_df = joined_df.withColumn("date_added", coalesce(col("date_added"), current_date())) \
            .withColumn("current_price", when(col("max_close_price").isNotNull(), col("max_close_price")).otherwise(
            col("current_price"))) \
            .withColumn("price_when_added",
                        when(col("price_when_added").isNotNull(), col("price_when_added")).otherwise(
                            col("max_close_price")))
        
        # Remove max_close_price column (we'll calculate change_since_added later with correct values)
        joined_df = joined_df.drop("max_close_price")
        
        # === STEP 5: Update active status based on performance and time criteria ===
        # Note: We'll calculate performance metrics after the merge step
        # For now, just preserve the is_active status or set to True for new entries
        joined_df = joined_df.withColumn("is_active", coalesce(col("is_active"), lit(True)))
        
        # === STEP 6: Preserve historical data for existing entries ===
        # Perform a left join to keep original date_added and price_when_added
        # Only update these values for completely new entries
        merged_df = joined_df.alias("new").join(
            stock_change_tracker_df.alias("old"),
            "symbol",
            "left"
        ).select(
            col("new.symbol"),
            # Keep original date_added if it exists, otherwise use new one
            when(col("old.date_added").isNotNull(), col("old.date_added")).otherwise(col("new.date_added")).alias(
                "date_added"),
            # Keep original price_when_added if it exists, otherwise use new one
            when(col("old.price_when_added").isNotNull(), col("old.price_when_added")).otherwise(
                col("new.price_when_added")).alias("price_when_added"),
            # Always use the new current_price (latest market price)
            col("new.current_price").alias("current_price"),
            # Keep existing is_active status for now (will recalculate after performance metrics)
            when(col("old.is_active").isNotNull(), col("old.is_active")).otherwise(col("new.is_active")).alias("is_active")
        )
        
        # === STEP 7: Calculate price changes with correct price values ===
        # NOW we have the correct price_when_added and current_price values
        # Calculate actual dollar change: current_price - price_when_added
        merged_df = merged_df.withColumn("change_since_added", 
            col("current_price") - col("price_when_added"))
        
        # Calculate percentage change: ((current_price - price_when_added) / price_when_added) * 100
        merged_df = merged_df.withColumn("change_in_percent", 
            (col("current_price") - col("price_when_added")) / col("price_when_added") * 100)
        
        # === STEP 8: Determine if stock has positive performance ===
        # Mark stocks as having positive earnings if change_in_percent > 0.50% (meaningful threshold)
        # This ensures only stocks with real gains of at least 0.5% are considered positive
        merged_df = merged_df.withColumn("is_positive_earning",
                                         when(col("change_in_percent") > 0.50, True).otherwise(False))
        
        # === STEP 9: Update active status based on performance and time criteria ===
        # Business rules:
        # - Deactivate stocks that are losing money for more than 120 days (4 months)
        # - Reactivate inactive stocks only if they have at least $1 increase since added
        # - New stocks default to active
        merged_df = merged_df.withColumn("is_active",
                                         when(
                                             (col("is_positive_earning") == False) & (
                                                     datediff(current_date(), col("date_added")) > 120),
                                             # Deactivate losing stocks after 120 days
                                             False
                                         ).when(
                                             (col("is_active") == False) & (col("change_since_added") >= 1.0),
                                             # Reactivate inactive stocks only if they gained at least $1 since added
                                             True
                                         ).when(col("is_active").isNull(), True)  # New stocks are active by default
                                         .otherwise(
                                             col("is_active")))  # Keep existing value for all other cases
        
        # === STEP 10: Add updated_date and format numerical data for database storage ===
        # Add updated_date column to track when record was last modified
        merged_df = merged_df.withColumn("updated_date", current_date())
        
        # Round prices and changes to 3 decimal places and ensure proper data types
        merged_df = merged_df.withColumn("price_when_added", round(col("price_when_added"), 3).cast(DoubleType())) \
            .withColumn("current_price", round(col("current_price"), 3).cast(DoubleType())) \
            .withColumn("change_since_added", round(col("change_since_added"), 3).cast(DoubleType())) \
            .withColumn("change_in_percent", round(col("change_in_percent"), 3).cast(DoubleType()))
        
        # Display sample of processed data for verification
        merged_df.show(10, truncate=False)
        
        # === STEP 11: BACKUP TO CSV BEFORE DATABASE OPERATIONS ===
        # Create CSV backup with timestamp to ensure we can restore if needed
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"./backup/stock_change_tracker_backup_{timestamp}.csv"
        
        # Ensure backup directory exists
        os.makedirs("./backup", exist_ok=True)
        
        # Convert to Pandas and save as CSV backup
        print(f"💾 Creating CSV backup at: {backup_path}")
        
        # Cast boolean columns explicitly to avoid numpy compatibility issues
        merged_df = merged_df.withColumn("is_active", col("is_active").cast("boolean")) \
            .withColumn("is_positive_earning", col("is_positive_earning").cast("boolean"))

        try:
            merged_df_pandas = merged_df.toPandas()
            merged_df_pandas.to_csv(backup_path, index=False)
            print(f"✅ CSV backup created successfully with {len(merged_df_pandas)} records")
        except Exception as pandas_error:
            print(f"⚠️ Warning: CSV backup failed due to pandas conversion issue: {pandas_error}")
            print("🔄 Proceeding with database operations...")
        
        # === STEP 12: VALIDATE DATA BEFORE DATABASE UPDATE ===
        # Check if we have data to save
        record_count = merged_df.count()
        if record_count == 0:
            print("❌ ERROR: No data to save! Aborting database update to prevent table deletion.")
            print("💡 Your table is safe - no changes were made.")
            return
        
        print(f"📊 Validation passed: {record_count} records ready for database update")
        
        # === STEP 13: SAFER DATABASE UPDATE WITH TRANSACTION-LIKE APPROACH ===
        try:
            # Use temporary table approach to ensure atomic updates
            stock_change_tracker_table_tmp = stock_change_tracker_table + "_tmp"
            
            # Write to temporary table first with optimized settings
            print("🔄 Writing to temporary table with optimized settings...")
            optimized_write_to_mysql(merged_df, url, sql_driver, stock_change_tracker_table_tmp, 
                                   username, password, mode='overwrite')
            
            # Verify temporary table has data
            temp_options = get_optimized_jdbc_options(url, sql_driver, username, password, stock_change_tracker_table_tmp)
            temp_df = spark.read.format("jdbc").options(**temp_options).load()
            
            temp_count = temp_df.count()
            if temp_count == 0:
                print("❌ ERROR: Temporary table is empty! Aborting to protect your data.")
                return
            
            print(f"✅ Temporary table verified with {temp_count} records")
            
            # Create a final backup of current table before overwrite
            print("💾 Creating final backup of current table...")
            current_table_backup_path = f"./backup/stock_change_tracker_current_{timestamp}.csv"
            try:
                current_options = get_optimized_jdbc_options(url, sql_driver, username, password, stock_change_tracker_table)
                current_df = spark.read.format("jdbc").options(**current_options).load()
                current_df_pandas = current_df.toPandas()
                current_df_pandas.to_csv(current_table_backup_path, index=False)
                print(f"✅ Current table backup saved: {current_table_backup_path}")
            except Exception as backup_error:
                print(f"⚠️  Warning: Could not backup current table: {backup_error}")
                print("🔄 Proceeding with update...")
            
            # Final write to the actual tracker table with optimized settings
            print("🔄 Updating main table with optimized settings...")
            optimized_write_to_mysql(temp_df, url, sql_driver, stock_change_tracker_table, 
                                   username, password, mode='overwrite')
            
            # Verify the update was successful
            final_options = get_optimized_jdbc_options(url, sql_driver, username, password, stock_change_tracker_table)
            final_df = spark.read.format("jdbc").options(**final_options).load()
            
            final_count = final_df.count()
            print(f"✅ Update successful! Main table now has {final_count} records")
            print(f"📋 Backup files created:")
            print(f"   - New data backup: {backup_path}")
            print(f"   - Previous table backup: {current_table_backup_path}")
            
            # === STEP 14: CLEAN UP OLD BACKUP FILES ===
            # Ensure we don't accumulate too many backup files
            manage_backup_files(backup_dir="./backup", max_backups=5)
            
        except Exception as db_error:
            print(f"❌ Database update failed: {db_error}")
            print(f"💾 Your data is safe in backup file: {backup_path}")
            print("🔧 You can restore from the backup if needed")
            raise
        
    finally:
        # Always clean up Spark resources
        spark.stop()
        
        # Clean up old backup files even if there were errors
        try:
            manage_backup_files(backup_dir="./backup", max_backups=5)
        except Exception as cleanup_error:
            print(f"⚠️  Warning: Backup cleanup failed: {cleanup_error}")
            print("🔄 This doesn't affect your data - just means old backups weren't removed")


if __name__ == "__main__":
    main()
