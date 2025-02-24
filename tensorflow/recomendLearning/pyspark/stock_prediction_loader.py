import yfinance as yf
import pandas as pd
from pyspark.sql import SparkSession, Window
from pyspark.sql.functions import col, to_timestamp, to_date, lit, row_number, max, abs, format_number
from pyspark.sql.types import StructType, StructField, StringType, DoubleType, TimestampType
from datetime import datetime, timedelta
import configparser
import os


def main():
    filename = "/Users/gaurav/workspace/datascience/chatgpt/tensorflow/recomendLearning/pyspark/practice/finance/model/output/*.csv"
    spark = SparkSession.builder.master("local[1]").appName("Stock Loader") \
        .config("spark.jars",
                "/Users/gaurav/.m2/repository/com/mysql/mysql-connector-j/8.0.33/mysql-connector-j-8.0.33.jar").getOrCreate()
    config = configparser.ConfigParser()
    config.read(os.path.join(os.path.dirname(__file__), 'conf/config.ini'))
    print("Sections : ", config.sections())
    sql_driver = "com.mysql.cj.jdbc.Driver"
    url = 'jdbc:mysql://localhost/{}'.format(config.get('mysql', 'database'))
    table = "stocks_prediction"
    stock_change_tracker_table = config.get('mysql', 'stock_change_tracker_table')
    username = config.get('mysql', 'username')
    password = config.get('mysql', 'password')
    print("url : ", url)
    print("MySQL User : ", username)

    stockdf = spark.read.format("jdbc").options(url=url,
                                                driver=sql_driver,
                                                dbtable=table,
                                                user=username,
                                                password=password).load()
    # List of stock symbols to process

    stk_new_df = spark.read.format("csv") \
        .option("quote", "\"") \
        .option("delimiter", ",") \
        .option("encoding", "UTF-8") \
        .option("escape", "\\").option("header", True).csv(filename)

    # stk_new_df.show(10)
    existing_data = stockdf.select("Date", "Symbol")
    filtered_data = stk_new_df.join(existing_data, ["Date", "Symbol"], "left_anti")
    filtered_data.show(5, truncate=False)
    filtered_data.write.format('jdbc').options(url=url,
                                               driver=sql_driver,
                                               dbtable=table,
                                               user=username,
                                               password=password).mode('append').save()
    spark.sparkContext.stop()


if __name__ == "__main__":
    main()
