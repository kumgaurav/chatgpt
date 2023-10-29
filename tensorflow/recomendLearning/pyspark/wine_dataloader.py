import os.path

from pyspark.sql import SparkSession
from pyspark.sql.functions import lit, col
import configparser


def main():
    config = configparser.ConfigParser()
    config.read(os.path.join(os.path.dirname(__file__), 'conf/config.ini'))
    url = 'jdbc:mysql://localhost/{}'.format(config.get('mysql', 'database'))
    tablename = "Wines"
    username = config.get('mysql', 'username')
    password = config.get('mysql', 'password')
    print("url : ", url)
    print("MySQL User : ", username)
    spark = SparkSession.builder.appName("wine loader").master("local[1]").getOrCreate()
    red_filename = "winequality-red"
    red_df = spark.read.option("quote", "\"").option("delimiter", ";") \
        .option("encoding", "UTF-8") \
        .option("escape", "\\") \
        .option("nullValue", "\\N") \
        .option("header", True) \
        .option("inferSchema", True) \
        .csv('./data/' + str(red_filename) + '.csv')
    red_df = red_df.withColumn("is_red", lit(1))
    red_df.show(10)
    white_filename = "winequality-white"
    wh_df = spark.read.option("quote", "\"").option("delimiter", ";") \
        .option("encoding", "UTF-8") \
        .option("escape", "\\") \
        .option("nullValue", "\\N") \
        .option("header", True)\
        .option("inferSchema", True) \
        .csv('./data/' + str(white_filename) + '.csv')
    wh_df = wh_df.withColumn("is_red", lit(0))
    wh_df.show(10)
    wine_df = red_df.union(wh_df)
    wine_df.printSchema()
    wine_df = wine_df.select([col(c).alias(c.replace(" ", "_").lower()) for c in wine_df.columns])
    wine_df.write.format("jdbc").options(url=url, driver="com.mysql.jdbc.Driver", user=username, password=password, dbtable=tablename).mode("overwrite").save()
    spark.sparkContext.stop()


if __name__ == "__main__":
    main()
