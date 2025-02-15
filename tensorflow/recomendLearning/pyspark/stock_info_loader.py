import yfinance as yf
import pandas as pd
from pyspark.sql import SparkSession, Window
from pyspark.sql.functions import col, to_timestamp, to_date, lit, row_number, max, abs, format_number
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
    # List of stock symbols to process
    stockdf = spark.read.format("jdbc").options(url=url,
                                                driver=sql_driver,
                                                dbtable=table,
                                                user=username,
                                                password=password).load()
    # Calculate the max date
    max_start_date = stockdf.agg(max("Date")).collect()[0][0]
    # stockdf.printSchema()
    print(f"max_date : {max_start_date}")
    stockdf = stockdf.filter(col("Date") == lit(max_start_date))
    # Assuming max_start_date is a datetime or string object
    stockdf = stockdf.withColumnRenamed("Open", "price_when_added") \
        .withColumnRenamed("Close", "current_price") \
        .withColumn("date_added", lit(max_start_date)) \
        .withColumn("change_since_added",
                    format_number(abs((col("current_price") - col("price_when_added")) / col("price_when_added")) * 100, 3))
    stockdf = stockdf\
        .withColumn("price_when_added", format_number(abs(col("price_when_added")), 3)) \
        .withColumn("current_price", format_number(abs(col("current_price")), 3))
    stockdf = stockdf.select("symbol", "date_added", "price_when_added", "current_price", "change_since_added")

    stockdf.show(10)
    stockdf.write.format('jdbc').options(url=url,
                                         driver=sql_driver,
                                         dbtable=stock_change_tracker_table,
                                         user=username,
                                         password=password).mode('overwrite').save()
    spark.sparkContext.stop()


if __name__ == "__main__":
    main()
