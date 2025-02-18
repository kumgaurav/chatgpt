import yfinance as yf
import pandas as pd
from pyspark.sql import SparkSession, Window
from pyspark.sql.functions import col, to_timestamp, to_date, lit, row_number, max
from pyspark.sql.types import StructType, StructField, StringType, DoubleType, TimestampType
from datetime import datetime, timedelta
import configparser
import os


def get_ticker(input_sym):
    """Initialize and return the ticker object for a given symbol."""
    try:
        ticker = yf.Ticker(input_sym)
        return ticker
    except Exception as e:
        print(f"Error fetching ticker for {input_sym}: {e}")
        return None


def get_earnings_data(ticker, input_sym):
    """Fetch earnings data from the ticker object."""
    try:
        earnings_dates = ticker.earnings_dates

        if earnings_dates is not None and not earnings_dates.empty:
            earnings_data_list = []

            for index, earnings_data_local in earnings_dates.iterrows():
                sym_earnings_date = earnings_data_local.name

                eps_estimate = earnings_data_local.get('EPS Estimate', None)
                reported_eps = earnings_data_local.get('Reported EPS', None)
                surprise_percent = earnings_data_local.get('Surprise(%)', None)

                earnings_data_list.append({
                    "Symbol": input_sym,
                    'earnings_date': sym_earnings_date,
                    'eps_estimate': eps_estimate,
                    'reported_eps': reported_eps,
                    'surprise_percent': surprise_percent
                })

            return earnings_data_list
        else:
            print(f"No earnings dates available for {input_sym}")
            return None
    except Exception as e:
        print(f"Error fetching earnings data for {input_sym}: {e}")
        return None


def get_quarterly_financials(ticker, input_sym):
    """Fetch quarterly financial data from the ticker object."""
    try:
        quarterly_financials = ticker.quarterly_income_stmt

        if quarterly_financials is not None and not quarterly_financials.empty:
            quarter_end_dates = quarterly_financials.columns
            financial_data_list = []

            for quarter_end in quarter_end_dates:
                total_revenue = quarterly_financials.loc['Total Revenue', quarter_end]
                financial_data_list.append({
                    'Symbol': input_sym,
                    'quarter_end_date': quarter_end.date(),
                    'total_revenue': total_revenue
                })

            return financial_data_list
        else:
            print(f"No quarterly financials available for {input_sym}")
            return None
    except Exception as e:
        print(f"Error fetching quarterly financials for {input_sym}: {e}")
        return None


# Convert Pandas DataFrame to format Spark can handle
def er_convert_df(local_df):
    df_copy = local_df.copy()
    # Convert datetime to string using the format
    df_copy['earnings_date'] = df_copy['earnings_date'].apply(
        lambda x: x.strftime('%Y-%m-%d %H:%M:%S') if isinstance(x, pd.Timestamp) and pd.notnull(x) else None)
    return df_copy


# Convert Pandas DataFrame to format Spark can handle
def qtr_convert_df(local_df):
    df_copy = local_df.copy()
    # Convert 'quarter_end_date' to datetime if it's not already
    df_copy['quarter_end_date'] = pd.to_datetime(df_copy['quarter_end_date'], errors='coerce')
    # Convert datetime to string using the format
    df_copy['quarter_end_date'] = df_copy['quarter_end_date'].apply(
        lambda x: x.strftime('%Y-%m-%d') if pd.notnull(x) else None)
    return df_copy


def main():
    spark = SparkSession.builder.master("local[1]").appName("Stock Loader") \
        .config("spark.jars",
                "/Users/gaurav/.m2/repository/com/mysql/mysql-connector-j/8.0.33/mysql-connector-j-8.0.33.jar").getOrCreate()
    config = configparser.ConfigParser()
    config.read(os.path.join(os.path.dirname(__file__), 'conf/config.ini'))
    print("Sections : ", config.sections())
    sql_driver = "com.mysql.cj.jdbc.Driver"
    url = 'jdbc:mysql://localhost/{}'.format(config.get('mysql', 'database'))
    table = config.get('mysql', 'earning_table')
    history_table = config.get('mysql', 'earning_table') + "_history"
    revenue_table = config.get('mysql', 'revenue_table')
    username = config.get('mysql', 'username')
    password = config.get('mysql', 'password')
    print("url : ", url)
    print("MySQL User : ", username)
    # List of stock symbols to process
    # symbols = ['TTD','PANW']
    symbols = config.get('stocks', 'symbols').split()
    # Initialize 'data' as an empty DataFrame with the expected columns
    er_data = pd.DataFrame(
        columns=['symbol', 'earnings_date', 'eps_estimate', 'reported_eps', 'surprise_percent'])
    qtr_data = pd.DataFrame(
        columns=['symbol', 'quarter_end_date', 'total_revenue'])
    for symbol in symbols:
        ticker = get_ticker(symbol)
        if not ticker:
            print(f"Skipping {symbol} as ticker could not be fetched")
            continue  # Skip the rest of the loop for this symbol if ticker is not fetched
        earnings_data = get_earnings_data(ticker, symbol)
        quarterly_data = get_quarterly_financials(ticker, symbol)
        # print(quarterly_data)
        if earnings_data:
            # Convert earnings data to a DataFrame
            earnings_df = pd.DataFrame(earnings_data)

            # Add the symbol to the earnings DataFrame
            earnings_df['symbol'] = symbol

            # Concatenate the new data and reassign to 'data'
            er_data = pd.concat([er_data, earnings_df], ignore_index=True)
        if quarterly_data:
            # Convert earnings data to a DataFrame
            quarterly_df = pd.DataFrame(quarterly_data)

            # Add the symbol to the earnings DataFrame
            quarterly_df['symbol'] = symbol

            # Concatenate the new data and reassign to 'data'
            qtr_data = pd.concat([qtr_data, quarterly_df], ignore_index=True)

    # Check the updated 'data' DataFrame
    # print(data.head())

    # Create pandas DataFrame from the collected data
    er_df = pd.DataFrame(er_data)
    qtr_df = pd.DataFrame(qtr_data)
    # print(qtr_df)
    # Define the schema explicitly to handle the datetime conversion properly
    # Define the schema
    schema = StructType([
        StructField("Symbol", StringType(), True),
        StructField("earnings_date", StringType(), True),
        StructField("eps_estimate", DoubleType(), True),
        StructField("reported_eps", DoubleType(), True),
        StructField("surprise_percent", DoubleType(), True)
    ])
    qtr_schema = StructType([
        StructField("Symbol", StringType(), True),
        StructField("quarter_end_date", StringType(), True),
        StructField("total_revenue", DoubleType(), True)
    ])

    # Convert to Spark DataFrame
    # Convert the pandas DataFrame to Spark DataFrame
    # Ensure the data is in a format that Spark can handle (list of dicts)
    er_df_converted = er_convert_df(er_df)
    qtr_df_converted = qtr_convert_df(qtr_df)
    # print(qtr_df_converted)
    er_spark_df = spark.createDataFrame(er_df_converted.to_dict(orient='records'), schema=schema)
    qtr_spark_df = spark.createDataFrame(qtr_df_converted.to_dict(orient='records'), schema=qtr_schema)
    # Get date threshold (last 3 weeks)
    date_threshold_start = datetime.today() - timedelta(weeks=3)
    date_threshold_end = datetime.today() + timedelta(weeks=14)
    print(f"date_threshold_start : {date_threshold_start}, date_threshold_end: {date_threshold_end}")
    earnings_df = er_spark_df.withColumn("earnings_date", to_date(col("earnings_date")))
    # Replace NaN and null values with a default value for all columns
    earnings_df = earnings_df.fillna({'eps_estimate': 0, 'reported_eps': 0, 'surprise_percent': 0})
    qtr_spark_df = qtr_spark_df.fillna({'total_revenue': 0})
    earnings_history_df = earnings_df
    # earnings_history_df = earnings_df.filter((col("eps_estimate") != 0) & (col("reported_eps") != 0))
    earnings_df = earnings_df.filter(
        (col("earnings_date") >= to_date(lit(date_threshold_start))) &
        (col("earnings_date") <= to_date(lit(date_threshold_end)))
    )
    # Show schema and first few records to verify data
    # spark_df.printSchema()

    next_earnings_df = earnings_df.filter((col("eps_estimate") == 0) & (col("reported_eps") == 0))
    # Define the window specification: partition by 'symbol' and order by 'earnings_date'
    window_spec = Window.partitionBy("Symbol").orderBy("earnings_date")

    # Apply the row_number() function to get ranks
    earnings_df_with_rank = next_earnings_df.withColumn("rank", row_number().over(window_spec))

    # Filter rows where rank is 1
    earnings_df_rank_1 = earnings_df_with_rank.filter(col("rank") == 1).drop(col("rank"))
    earnings_df_rank_1.cache()
    earnings_df_rank_1.show(10)
    # earnings_df_rank_1.write.format('jdbc').options(url=url,
    #                                                 driver=sql_driver,
    #                                                 dbtable=table,
    #                                                 user=username,
    #                                                 password=password).mode('overwrite').save()
    earnings_history_df.write.format('jdbc').options(url=url,
                                                     driver=sql_driver,
                                                     dbtable=history_table,
                                                     user=username,
                                                     password=password).mode('overwrite').save()
    qtr_spark_df = qtr_spark_df.withColumn("low_revenue_estimate", lit(0)).withColumn("avg_revenue_estimate", lit(0))
    # Load the existing table data
    df_table = spark.read.format("jdbc").options(url=url,
                                                 driver=sql_driver,
                                                 dbtable=revenue_table,
                                                 user=username,
                                                 password=password).load()
    # Ensure quarter_end_date is in date format
    df_table = df_table.withColumn("quarter_end_date", to_date(col("quarter_end_date")))
    # Get the latest quarter_end_date per symbol in the table
    df_latest = df_table.groupBy("Symbol").agg(max(col("quarter_end_date")).alias("latest_date"))

    # Join with new data to keep only rows with newer quarter_end_date
    df_filtered = qtr_spark_df.alias("new").join(df_latest.alias("latest"),
                                                 (col("new.Symbol") == col("latest.Symbol")), "left") \
        .filter((col("latest.latest_date").isNull()) | (col("new.quarter_end_date") > col("latest.latest_date"))) \
        .select("new.*")  # Retain only new rows

    df_filtered.show(10)
    df_filtered.write.format('jdbc').options(url=url,
                                             driver=sql_driver,
                                             dbtable=revenue_table,
                                             user=username,
                                             password=password).mode('append').save()
    spark.sparkContext.stop()


if __name__ == "__main__":
    main()
