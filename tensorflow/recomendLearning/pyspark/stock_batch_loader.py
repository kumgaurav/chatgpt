import yfinance as yf
from pyspark.sql import SparkSession, Window
from pyspark import SparkConf, SparkContext
from pandas_datareader import data as pdr
from datetime import date
import pandas as pd
import os
import configparser
from pprint import pprint
from pyspark.sql.functions import lit, col, max, lag, when, trim
from datetime import datetime, timedelta

# Create a mapping for period codes to meaningful names
period_mapping = {
    "0q": "Current Quarter",
    "+1q": "Next Quarter",
    "0y": "Current Year",
    "+1y": "Next Year",
    "LTG": "Long-Term Growth"
}


def get_data(tickers, start_date, end_date):
    # 📌 Download stock price data
    data = yf.download(tickers, start=start_date, progress=False)  # , end=end_date it's not required
    # ✅ Convert MultiIndex columns to normal DataFrame with tickers as a column
    data = data.stack(level=1, future_stack=True).reset_index()
    data.rename(columns={"level_1": "Ticker"}, inplace=True)
    # ✅ Save stock prices
    price_filename = f"data/stock_prices_{start_date}.csv"
    data.to_csv(price_filename, index=False)
    print(f"📁 Stock prices saved as {price_filename}")
    return price_filename


def main():
    print(f"Running batch loader")
    spark = SparkSession.builder.master("local[1]").appName("Stock Loader") \
        .config("spark.jars",
                "/Users/gaurav/.m2/repository/com/mysql/mysql-connector-j/8.0.33/mysql-connector-j-8.0.33.jar").getOrCreate()
    config = configparser.ConfigParser()
    config.read(os.path.join(os.path.dirname(__file__), 'conf/config.ini'))
    print("Sections : ", config.sections())
    sql_driver = "com.mysql.cj.jdbc.Driver"
    url = 'jdbc:mysql://localhost/{}'.format(config.get('mysql', 'database'))
    table = config.get('mysql', 'table')
    stock_change_tracker_table = stock_change_tracker_table = "stock_change_tracker"
    username = config.get('mysql', 'username')
    password = config.get('mysql', 'password')
    # ticker_list = ["GSHD","MCD","DFH","GRBK","ENSG","AWI","TMCH","ODD","STRL","NOVT","ITRI","BUD"]
    # ticker_list = config.get('stocks', 'symbols').split()
    change_tracker_df = spark.read.format("jdbc").options(url=url, driver=sql_driver, user=username, password=password,
                                                          dbtable=stock_change_tracker_table).load()
    change_tracker_df = change_tracker_df.filter(col("is_active") == True).select("symbol")
    ticker_list = change_tracker_df.rdd.flatMap(lambda x: x).collect()
    # change_tracker_df.show(10, truncate=False)
    print("ticker_list : ", ticker_list)
    start_date = "2024-06-01"
    end_date = "2025-02-22"
    today = date.today()
    end_date = today.strftime("%Y-%m-%d")
    stockdf = spark.read.format("jdbc").options(url=url,
                                                driver=sql_driver,
                                                dbtable=table,
                                                user=username,
                                                password=password).load()
    # stockdf.show(2)
    max_start_date = stockdf.agg(max("Date")).collect()[0][0]
    start_date = max_start_date
    print("start_date : ", start_date)
    print("end_date : ", end_date)
    price_filename = get_data(ticker_list, start_date, end_date)
    # price_filename = "data/stock_prices_2024-06-01.csv"
    print("filename : ", price_filename)
    stk_new_df = spark.read.option("header", True).csv(price_filename)
    stk_new_df = stk_new_df.withColumnRenamed("Ticker", "Symbol").withColumn("Adj Close", col("Close"))
    stk_new_df = stk_new_df.filter(col("Open").isNotNull() & (trim(col("Open")) != ""))
    # You can sort the DataFrame by date to ensure consecutive dates are in order
    stk_new_df = stk_new_df.orderBy("date")
    # Define a window specification for the lag function
    window_spec = Window.partitionBy("Symbol").orderBy("Date")
    # Calculate the lag of Close price
    prev_close = lag("Close").over(window_spec)
    # Calculate the price change by subtracting the previous day's close_price from the current day's close_price
    stk_new_df = stk_new_df.withColumn("price_change", when(prev_close.isNull(), col("Close") - col("Open")).otherwise(
        col("Close") - prev_close))
    # stk_new_df.show(2)
    existing_data = stockdf.select("Date", "Symbol")
    # Perform an anti-join to get only new records
    filtered_data = stk_new_df.join(existing_data, ["Date", "Symbol"], "left_anti")
    filtered_data.show(5, truncate=False)
    filtered_data.write.format('jdbc').options(url=url,
                                               driver=sql_driver,
                                               dbtable=table,
                                               user=username,
                                               password=password).mode('append').save()

    spark.stop()


if __name__ == "__main__":
    main()
