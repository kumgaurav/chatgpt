from pyspark.sql import SparkSession, Window
from pyspark import SparkConf, SparkContext
from pandas_datareader import data as pdr
from datetime import date
import yfinance as yf
import pandas as pd
import os
import configparser

from pyspark.sql.functions import lit, col, max, lag


# Create a data folder in your current dir.
def save_data(df, filename):
    df.to_csv('./data/' + filename + '.csv')


def get_data(ticker, start_date, end_date, files):
    print(ticker)
    data = yf.download(ticker, start=start_date, end=end_date, progress=False)
    dataname = ticker + '_' + str(end_date)
    files.append(dataname)
    save_data(data, dataname)
    return dataname


def main():
    spark = SparkSession.builder.master("local[1]").appName("Stock Loader") \
        .config("spark.jars",
                "/Users/gaurav/.m2/repository/com/mysql/mysql-connector-j/8.0.33/mysql-connector-j-8.0.33.jar").getOrCreate()
    config = configparser.ConfigParser()
    config.read(os.path.join(os.path.dirname(__file__), 'conf/config.ini'))
    print("Sections : ", config.sections())

    url = 'jdbc:mysql://localhost/{}'.format(config.get('mysql', 'database'))
    table = config.get('mysql', 'table')
    username = config.get('mysql', 'username')
    password = config.get('mysql', 'password')
    print("url : ", url)
    print("MySQL User : ", username)
    # We can get data by our choice by giving days bracket
    start_date = "2017-01-01"
    max_start_date = None
    today = date.today()
    end_date = today.strftime("%Y-%m-%d")
    ticker_list = config.get('stocks', 'symbols').split()
    print("ticker_list : ", ticker_list)
    stockdf = spark.read.format("jdbc").options(url=url,
                                                driver='com.mysql.jdbc.Driver',
                                                dbtable=table,
                                                user=username,
                                                password=password).load()
    max_start_date = stockdf.agg(max("Date")).collect()[0][0]
    if max_start_date:
        start_date = max_start_date
    print("max_start_date : ", max_start_date)
    print("start_date : ", start_date)
    print("end_date : ", end_date)
    if end_date == max_start_date:
        print("Nothing to bring and existing")
        spark.sparkContext.stop()
        exit(0)
    files = []
    for tik in ticker_list:
        filename = get_data(tik, start_date, end_date, files)
        dft = spark.read.option("header", True).csv('./data/' + str(filename) + '.csv')
        df = dft.withColumn("symbol", lit(tik))
        max_start_date_tic = df.agg(max("Date")).collect()[0][0]
        print("max_start_date_tic : ", max_start_date_tic)
        print("str(max_start_date_tic) == str(max_start_date) : ", (str(max_start_date_tic) == str(max_start_date)))
        if str(max_start_date_tic) == str(max_start_date):
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
                                        driver='com.mysql.jdbc.Driver',
                                        dbtable=table,
                                        user=username,
                                        password=password).mode('append').save()

    spark.sparkContext.stop()


if __name__ == "__main__":
    main()
