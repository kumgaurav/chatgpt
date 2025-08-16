import os
import sys
import configparser
from pyspark.sql import SparkSession


def build_spark(app_name: str, cfg: configparser.ConfigParser) -> SparkSession:
    # Keep Python executable consistent across driver/executors
    python_path = sys.executable
    os.environ['PYSPARK_PYTHON'] = python_path
    os.environ['PYSPARK_DRIVER_PYTHON'] = python_path

    mysql_jar = cfg.get('spark', 'mysql_jar_path', fallback='')
    builder = SparkSession.builder.appName(app_name).master('local[*]')
    if mysql_jar and os.path.exists(mysql_jar):
        builder = builder.config('spark.jars', mysql_jar)

    # Reasonable defaults; callers can adjust if needed later.
    builder = builder.config('spark.sql.adaptive.enabled', 'true') 
    builder = builder.config('spark.sql.adaptive.coalescePartitions.enabled', 'true')

    return builder.getOrCreate()


