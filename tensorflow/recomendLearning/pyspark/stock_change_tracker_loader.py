import yfinance as yf
import pandas as pd
from pyspark.sql import SparkSession, Window
from pyspark.sql.functions import col, to_timestamp, to_date, lit, row_number, max, abs, format_number, coalesce, \
    current_date, when, datediff, round
from pyspark.sql.types import StructType, StructField, StringType, DoubleType, TimestampType
from datetime import datetime, timedelta
import configparser
import os


def main():
    spark = SparkSession.builder.master("local[1]").appName("Stock Loader") \
        .config("spark.jars",
                "/Users/gaurav/.m2/repository/com/mysql/mysql-connector-j/8.0.33/mysql-connector-j-8.0.33.jar").getOrCreate()
    config = configparser.ConfigParser()
    config.read(os.path.join(os.path.dirname(__file__), 'conf/config.ini'))
    print("Sections : ", config.sections())
    sql_driver = "com.mysql.cj.jdbc.Driver"
    url = 'jdbc:mysql://localhost/{}'.format(config.get('mysql', 'database'))
    table = config.get('mysql', 'table')
    stock_change_tracker_table = config.get('mysql', 'stock_change_tracker_table')
    username = config.get('mysql', 'username')
    password = config.get('mysql', 'password')
    print("url : ", url)
    print("MySQL User : ", username)
    try:
        # List of stock symbols to process
        stockdf = spark.read.format("jdbc").options(url=url,
                                                    driver=sql_driver,
                                                    dbtable=table,
                                                    user=username,
                                                    password=password).load()
        stock_change_tracker_df = spark.read.format("jdbc").options(url=url,
                                                                    driver=sql_driver,
                                                                    dbtable=stock_change_tracker_table,
                                                                    user=username,
                                                                    password=password).load()
        # Step 1: Find the max date for each symbol
        max_date_df = stockdf.groupBy("symbol").agg(max("Date").alias("max_date"))
        max_date_df = max_date_df.withColumnRenamed("symbol", "max_symbol")
        # Step 2: Join back to get the close price for the max date
        max_close_price_df = stockdf.join(max_date_df, (stockdf.symbol == max_date_df.max_symbol) & (
                stockdf.Date == max_date_df.max_date), "inner") \
            .select("symbol", "Close")
        # Rename the close column for clarity
        max_close_price_df = max_close_price_df.withColumnRenamed("Close", "max_close_price")
        # max_close_price_df.show(10, truncate=False)
        # stock_change_tracker_df.printSchema()
        # Step 1: Perform left join to get the data from max_close_price_df
        joined_df = max_close_price_df.join(stock_change_tracker_df, "symbol", "left")

        # Step 2: Coalesce the date_added column to current_date if it is null
        joined_df = joined_df.withColumn("date_added", coalesce(col("date_added"), current_date())) \
            .withColumn("current_price", when(col("max_close_price").isNotNull(), col("max_close_price")).otherwise(
            col("current_price"))) \
            .withColumn("price_when_added",
                        when(col("price_when_added").isNotNull(), col("price_when_added")).otherwise(
                            col("max_close_price")))
        # joined_df.filter(col("symbol") == lit("NFLX")).show(10, truncate=False)
        # Step 3: Calculate price_change as percentage difference
        joined_df = joined_df.withColumn("change_since_added", format_number(
            (col("current_price") - col("price_when_added")) / col("price_when_added") * 100, 3))
        # Step 4: Add is_positive_earning column based on change_since_added
        joined_df = joined_df.withColumn("is_positive_earning",
                                         when(col("change_since_added") > 0, True).otherwise(False)).drop(
            "max_close_price")
        # Step 5: Coalesce is_active to True if it is null
        # joined_df = joined_df.withColumn("is_active", coalesce(col("is_active"), lit(True)))
        joined_df = joined_df.withColumn("is_active", lit(True))
        # Step 6: Add is_active column based on the conditions
        # joined_df.filter(col("symbol") == lit("NFLX")).show(10, truncate=False)
        joined_df = joined_df.withColumn("is_active",
                                         when(
                                             (col("is_positive_earning") == False) & (
                                                     datediff(current_date(), col("date_added")) > 120),
                                             # 120 days > 4 months
                                             False
                                         ).when(
                                             (col("is_positive_earning") == True) & (col("is_active") == False),
                                             # only activate when is_positive_earning is True
                                             True
                                         ).otherwise(
                                             col("is_active")))  # Keep existing value of is_active if no conditions match
        # joined_df.filter(col("symbol") == lit("NFLX")).show(10, truncate=False)
        # Perform a left join to preserve existing values
        merged_df = joined_df.alias("new").join(
            stock_change_tracker_df.alias("old"),
            "symbol",
            "left"
        ).select(
            col("new.symbol"),
            when(col("old.date_added").isNotNull(), col("old.date_added")).otherwise(col("new.date_added")).alias(
                "date_added"),
            when(col("old.price_when_added").isNotNull(), col("old.price_when_added")).otherwise(
                col("new.price_when_added")).alias("price_when_added"),
            when(col("old.current_price").isNotNull(), col("old.current_price")).otherwise(
                col("new.current_price")).alias("current_price"),
            col("new.change_since_added"),
            col("new.is_positive_earning"),
            col("new.is_active")
        )
        # merged_df.filter(col("symbol") == lit("NFLX")).show(10, truncate=False)
        merged_df = merged_df.withColumn("price_when_added", round(col("price_when_added"), 3).cast(DoubleType())) \
            .withColumn("current_price", round(col("current_price"), 3).cast(DoubleType())) \
            .withColumn("change_since_added", round(col("change_since_added"), 3).cast(DoubleType()))
        # merged_df.filter(col("symbol") == lit("NFLX")).show(10, truncate=False)
        merged_df.show(10, truncate=False)
        stock_change_tracker_table_tmp = stock_change_tracker_table + "_tmp"
        merged_df.write.format('jdbc').options(url=url,
                                               driver=sql_driver,
                                               dbtable=stock_change_tracker_table_tmp,
                                               user=username,
                                               password=password).mode('overwrite').save()
        stock_change_tracker_tmp_df = spark.read.format("jdbc").options(url=url,
                                                                        driver=sql_driver,
                                                                        dbtable=stock_change_tracker_table_tmp,
                                                                        user=username,
                                                                        password=password).load()
        stock_change_tracker_tmp_df.write.format('jdbc').options(url=url,
                                                                 driver=sql_driver,
                                                                 dbtable=stock_change_tracker_table,
                                                                 user=username,
                                                                 password=password).mode('overwrite').save()
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
