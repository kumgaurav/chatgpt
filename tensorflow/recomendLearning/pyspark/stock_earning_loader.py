import yfinance as yf
import pandas as pd
from pyspark.sql import SparkSession, Window
from pyspark.sql.functions import col, to_timestamp, to_date, lit, row_number
from pyspark.sql.types import StructType, StructField, StringType, DoubleType, TimestampType
from datetime import datetime, timedelta
import configparser
import os


# Function to fetch earnings dates using yfinance API
def get_earnings_data(input_sym):
    try:
        ticker = yf.Ticker(input_sym)
        earnings_dates = ticker.earnings_dates

        # Check if earnings_dates is not empty
        if earnings_dates is not None and not earnings_dates.empty:
            # Drop rows with NaN values
            # earnings_dates = earnings_dates.dropna()

            # Check if there are any rows left after dropping NaNs
            if not earnings_dates.empty:
                # Create a list to store the earnings data
                earnings_data_list = []

                # Loop through each row in the DataFrame
                for index, earnings_data_local in earnings_dates.iterrows():
                    sym_earnings_date = earnings_data_local.name  # Earnings Date (DatetimeIndex)

                    # Extract the relevant data from each row
                    eps_estimate = earnings_data_local.get('EPS Estimate', None)
                    reported_eps = earnings_data_local.get('Reported EPS', None)
                    surprise_percent = earnings_data_local.get('Surprise(%)', None)

                    # Handle missing data in any column
                    # if eps_estimate is None or reported_eps is None or surprise_percent is None:
                    #    print(f"Missing some data for {input_sym} on {sym_earnings_date}")
                    #    continue  # Skip this row if any data is missing

                    # Append the data for this row to the list
                    earnings_data_list.append({
                        "Symbol": input_sym,
                        'earnings_date': sym_earnings_date,
                        'eps_estimate': eps_estimate,
                        'reported_eps': reported_eps,
                        'surprise_percent': surprise_percent
                    })

                # If data is available, return the list of earnings data
                if earnings_data_list:
                    return earnings_data_list
                else:
                    print(f"No valid earnings data found for {input_sym}")
                    return None
            else:
                print(f"No valid earnings data available for {input_sym}")
                return None
        else:
            print(f"No earnings dates available for {input_sym}")
            return None
    except Exception as e:
        print(f"Error fetching data for {input_sym}: {e}")
        return None


def main():
    spark = SparkSession.builder.master("local[1]").appName("Stock Loader") \
        .config("spark.jars",
                "/Users/gaurav/.m2/repository/com/mysql/mysql-connector-j/8.0.33/mysql-connector-j-8.0.33.jar").getOrCreate()
    config = configparser.ConfigParser()
    config.read(os.path.join(os.path.dirname(__file__), 'conf/config.ini'))
    print("Sections : ", config.sections())

    url = 'jdbc:mysql://localhost/{}'.format(config.get('mysql', 'database'))
    table = config.get('mysql', 'earning_table')
    username = config.get('mysql', 'username')
    password = config.get('mysql', 'password')
    print("url : ", url)
    print("MySQL User : ", username)
    # List of stock symbols to process
    symbols = ['AAPL', 'PANW']
    symbols = config.get('stocks', 'symbols').split()
    # Initialize 'data' as an empty DataFrame with the expected columns
    data = pd.DataFrame(columns=['symbol', 'earnings_date', 'eps_estimate', 'reported_eps', 'surprise_percent'])

    for symbol in symbols:
        earnings_data = get_earnings_data(symbol)
        if earnings_data:
            # Convert earnings data to a DataFrame
            earnings_df = pd.DataFrame(earnings_data)

            # Add the symbol to the earnings DataFrame
            earnings_df['symbol'] = symbol

            # Concatenate the new data and reassign to 'data'
            data = pd.concat([data, earnings_df], ignore_index=True)

    # Check the updated 'data' DataFrame
    # print(data.head())

    # Create pandas DataFrame from the collected data
    df = pd.DataFrame(data)
    # print(df)
    # Define the schema explicitly to handle the datetime conversion properly
    # Define the schema
    schema = StructType([
        StructField("Symbol", StringType(), True),
        StructField("earnings_date", StringType(), True),
        StructField("eps_estimate", DoubleType(), True),
        StructField("reported_eps", DoubleType(), True),
        StructField("surprise_percent", DoubleType(), True)
    ])

    # Convert Pandas DataFrame to format Spark can handle
    def convert_df(earning_df):
        df_copy = earning_df.copy()
        # Convert datetime to string using the format
        df_copy['earnings_date'] = df_copy['earnings_date'].apply(
            lambda x: x.strftime('%Y-%m-%d %H:%M:%S') if isinstance(x, pd.Timestamp) and pd.notnull(x) else None)
        return df_copy

    # Convert to Spark DataFrame
    # Convert the pandas DataFrame to Spark DataFrame
    # Ensure the data is in a format that Spark can handle (list of dicts)
    df_converted = convert_df(df)
    spark_df = spark.createDataFrame(df_converted.to_dict(orient='records'), schema=schema)
    # Get date threshold (last 3 weeks)
    date_threshold_start = datetime.today() - timedelta(weeks=3)
    date_threshold_end = datetime.today() + timedelta(weeks=14)
    print(f"date_threshold_start : {date_threshold_start}, date_threshold_end: {date_threshold_end}")
    earnings_df = spark_df.withColumn("earnings_date", to_date(col("earnings_date")))
    earnings_df = earnings_df.filter(
        (col("earnings_date") >= to_date(lit(date_threshold_start))) &
        (col("earnings_date") <= to_date(lit(date_threshold_end)))
    )
    # Show schema and first few records to verify data
    # spark_df.printSchema()
    # Replace NaN and null values with a default value for all columns
    earnings_df = earnings_df.fillna({'eps_estimate': 0, 'reported_eps': 0, 'surprise_percent': 0})
    earnings_df = earnings_df.filter((col("eps_estimate") == 0) & (col("reported_eps") == 0))
    # Define the window specification: partition by 'symbol' and order by 'earnings_date'
    window_spec = Window.partitionBy("Symbol").orderBy("earnings_date")

    # Apply the row_number() function to get ranks
    earnings_df_with_rank = earnings_df.withColumn("rank", row_number().over(window_spec))

    # Filter rows where rank is 1
    earnings_df_rank_1 = earnings_df_with_rank.filter(col("rank") == 1).drop(col("rank"))
    earnings_df_rank_1.cache()
    earnings_df_rank_1.show(10)
    earnings_df_rank_1.write.format('jdbc').options(url=url,
                                             driver='com.mysql.jdbc.Driver',
                                             dbtable=table,
                                             user=username,
                                             password=password).mode('overwrite').save()
    spark.sparkContext.stop()


if __name__ == "__main__":
    main()
