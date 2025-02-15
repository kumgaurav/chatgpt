from pyspark.sql import SparkSession, Window
from pyspark import SparkConf, SparkContext
from pandas_datareader import data as pdr
from datetime import date
import yfinance as yf
import pandas as pd
import os
import configparser
from pprint import pprint
from pyspark.sql.functions import lit, col, max, lag
from datetime import datetime, timedelta

directory = "./data"


def clean_dir():
    for file in os.listdir(directory):
        file_path = os.path.join(directory, file)
        if os.path.isfile(file_path):  # Ensure it's a file, not a subdirectory
            os.remove(file_path)


# Create a data folder in your current dir.
def save_data(df, filename):
    df.to_csv('./data/' + filename + '.csv')


def get_data(ticker, start_date, end_date, files):
    print(ticker)
    data = yf.download(ticker, group_by='Ticker', start=start_date, end=end_date, progress=False)
    data = data.stack(level=0).rename_axis(['Date', 'Ticker']).reset_index(level=1)
    data["symbol"] = ticker
    # Add 'price_change' column (Close Price - Previous Close)
    # data["price_change"] = data["Close"] - data["Open"]  # NaN for the first row
    data["Adj Close"] = data["Close"]
    data = data.drop(columns=["Ticker"])
    file_name = ticker + '_' + str(end_date)
    files.append(file_name)
    save_data(data, file_name)
    return file_name


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
    username = config.get('mysql', 'username')
    password = config.get('mysql', 'password')
    print("url : ", url)
    print("MySQL User : ", username)
    # We can get data by our choice by giving days bracket
    start_date = "2024-06-01"
    max_start_date = None
    today = date.today()
    end_date = today.strftime("%Y-%m-%d")
    ticker_list = config.get('stocks', 'symbols').split()
    print("ticker_list : ", ticker_list)
    stockdf = spark.read.format("jdbc").options(url=url,
                                                driver=sql_driver,
                                                dbtable=table,
                                                user=username,
                                                password=password).load()
    # stockdf.show(2)
    # max_start_date = stockdf.agg(max("Date")).collect()[0][0]
    # Group by 'symbol' and calculate the maximum date for each symbol
    max_date_by_symbol_df = stockdf.groupBy("symbol").agg(max("Date").alias("max_date"))
    # Convert max_date_by_symbol_df to a dictionary for lookup
    max_date_by_symbol_dict = {row["symbol"]: row["max_date"] for row in max_date_by_symbol_df.collect()}
    # pprint(max_date_by_symbol_dict)
    # Broadcast the dictionary for distributed lookup
    broadcast_max_dates = spark.sparkContext.broadcast(max_date_by_symbol_dict)
    if end_date == today:
        print("Nothing to bring and existing")
        spark.sparkContext.stop()
        exit(0)
    files = []
    for tik in ticker_list:
        max_sync_date = broadcast_max_dates.value.get(tik, start_date)
        if max_sync_date != start_date:  # Check if the date exists in the dictionary
            # Check if max_sync_date is a string, and parse only if necessary
            if isinstance(max_sync_date, str):
                max_sync_date = datetime.strptime(max_sync_date, "%Y-%m-%d").date()  # Convert to date
            # Increment the date by 1 day
            max_sync_date = max_sync_date + timedelta(days=1)
            max_start_date = max_sync_date
        print(f"max_sync_date : {max_sync_date} for symbol : {tik}")
        start_date = max_sync_date
        print("max_start_date : ", max_start_date)
        print("start_date : ", start_date)
        print("end_date : ", end_date)
        filename = get_data(tik, start_date, end_date, files)
        print("filename : ", filename)
        dft = spark.read.option("header", True).csv('./data/' + str(filename) + '.csv')
        df = dft.withColumn("symbol", lit(tik))
        df.show(2, truncate=False)
        if df.rdd.isEmpty():
            print("Nothing to bring for symbol : ", tik)
            continue
        max_start_date_tic = df.agg(max("Date")).collect()[0][0]
        if isinstance(max_start_date_tic, str):
            max_start_date_tic = datetime.strptime(max_start_date_tic, "%Y-%m-%d").date()
        if isinstance(max_start_date, str):
            max_start_date = datetime.strptime(max_start_date, "%Y-%m-%d").date()
        if max_start_date is None:
            max_start_date = datetime.strptime(max_sync_date, "%Y-%m-%d").date()
        print("max_start_date_tic : ", max_start_date_tic)
        print("max_start_date: ", max_start_date)
        print("today: ", today)
        print("str(max_start_date_tic) == str(max_start_date) : ", (max_start_date_tic == today))
        if max_start_date_tic == today or max_start_date > max_start_date_tic:
            print("Nothing to bring for symbol : ", tik)
            continue
        # Assuming you have a DataFrame named 'df' with columns 'date' and 'close_price'
        # You can sort the DataFrame by date to ensure consecutive dates are in order
        df = df.orderBy("date")
        # Define a window specification for the lag function
        window_spec = Window.orderBy("date")
        # Calculate the price change by subtracting the previous day's close_price from the current day's close_price
        df = df.withColumn("price_change", df["Close"] - lag(df["Close"]).over(window_spec))
        df.head()
        df.show(10)
        df.write.format('jdbc').options(url=url,
                                        driver=sql_driver,
                                        dbtable=table,
                                        user=username,
                                        password=password).mode('append').save()

    spark.sparkContext.stop()


if __name__ == "__main__":
    clean_dir()
    main()
