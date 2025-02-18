from pyspark.sql import SparkSession

# Define default Spark configurations
DEFAULT_CONFIGS = {
    "spark.sql.shuffle.partitions": "200",
    "spark.executor.memory": "4g",
    "spark.driver.extraClassPath": "/Users/gaurav/.m2/repository/com/mysql/mysql-connector-j/8.0.33/mysql-connector-j-8.0.33.jar",
    # Update with the correct path
}


def get_spark(app_name="MyApp", extra_configs=None):
    builder = SparkSession.builder.appName(app_name)

    # Apply default configurations
    for key, value in DEFAULT_CONFIGS.items():
        builder = builder.config(key, value)

    # Apply additional user-provided configurations
    if extra_configs:
        for key, value in extra_configs.items():
            builder = builder.config(key, value)

    spark = builder.enableHiveSupport().getOrCreate()
    return spark


def set_sql_config(spark):
    spark.conf.set("spark.sql.legacy.timeParserPolicy", "LEGACY")
    spark.conf.set("spark.sql.execution.arrow.pyspark.enabled", "true")  # Updated key


def initialize_spark(app_name="MyApp", extra_configs=None):
    spark = get_spark(app_name, extra_configs)
    set_sql_config(spark)
    return spark
