from typing import List
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql import types as T
from pyspark.sql.window import Window
import pandas as pd
import numpy as np
import json
import datetime as _dt


def _read_table(spark: SparkSession,
                url: str,
                driver: str,
                username: str,
                password: str,
                table: str) -> DataFrame:
    try:
        return spark.read.format('jdbc').options(
            url=url,
            driver=driver,
            dbtable=table,
            user=username,
            password=password,
        ).load()
    except Exception:
        # If table does not exist yet, signal empty by returning None
        return None


def _append_missing(spark: SparkSession,
                    df_new: DataFrame,
                    table: str,
                    key_columns: List[str],
                    url: str,
                    driver: str,
                    username: str,
                    password: str) -> int:
    if df_new is None or len(df_new.columns) == 0:
        return 0

    existing_df = _read_table(spark, url, driver, username, password, table)

    # If existing_df is truly empty (no rows and no schema), write all
    existing_keys = None
    if existing_df is not None:
        # Align key column types with existing table schema to avoid anti-join mismatches
        existing_schema = {f.name: f.dataType for f in existing_df.schema.fields}
        for key in key_columns:
            if key in df_new.columns and key in existing_schema:
                target_type = existing_schema[key]
                # Use to_date/to_timestamp explicitly for date-like types, else cast
                if isinstance(target_type, T.DateType):
                    df_new = df_new.withColumn(key, F.to_date(F.col(key)))
                elif isinstance(target_type, T.TimestampType):
                    df_new = df_new.withColumn(key, F.to_timestamp(F.col(key)))
                else:
                    df_new = df_new.withColumn(key, F.col(key).cast(target_type))
        try:
            existing_keys = existing_df.select(*key_columns).distinct()
        except Exception:
            existing_keys = None

    if existing_keys is None or existing_keys.rdd.isEmpty():
        to_write = df_new
    else:
        to_write = df_new.alias('n').join(existing_keys.alias('e'), key_columns, 'left_anti')

    # Replace NaN in floating columns with nulls to satisfy JDBC/MySQL
    for field in to_write.schema.fields:
        if isinstance(field.dataType, (T.DoubleType, T.FloatType)):
            to_write = to_write.withColumn(field.name, F.when(F.isnan(F.col(field.name)) | F.isnull(F.col(field.name)), None).otherwise(F.col(field.name)))

    count_to_write = to_write.count()
    if count_to_write > 0:
        to_write.write.format('jdbc').options(
            url=url,
            driver=driver,
            dbtable=table,
            user=username,
            password=password,
            batchsize='5000',
        ).mode('append').save()
    return count_to_write


def write_stock_earnings(spark: SparkSession, df: DataFrame, url: str, driver: str, username: str, password: str,
                             table: str = 'stock_earnings') -> int:
    return _append_missing(spark, df, table, ['ticker', 'earnings_date'], url, driver, username, password)


def write_last_stock_earnings(spark: SparkSession, df: DataFrame, url: str, driver: str, username: str, password: str,
                             table: str = 'last_stock_earnings') -> int:
    if df is None or len(df.columns) == 0:
        return 0
    window = Window.partitionBy('ticker').orderBy(F.col('earnings_date').desc())
    df = df.withColumn('rn', F.row_number().over(window)).where(F.col('rn') == 1).drop('rn')
    return _append_missing(spark, df, table, ['ticker'], url, driver, username, password)

def write_earnings_estimates(spark: SparkSession, df: DataFrame, url: str, driver: str, username: str, password: str,
                             table: str = 'earnings_estimates') -> int:
    # Primary key: (ticker, period, earnings_date)
    return _append_missing(spark, df, table, ['ticker', 'period', 'earnings_date'], url, driver, username, password)


def write_earnings_history(spark: SparkSession, df: DataFrame, url: str, driver: str, username: str, password: str,
                           table: str = 'earnings_history') -> int:
    return _append_missing(spark, df, table, ['ticker', 'earnings_date'], url, driver, username, password)


def write_quarterly_revenue(spark: SparkSession, df: DataFrame, url: str, driver: str, username: str, password: str,
                            table: str = 'quarterly_revenue') -> int:
    return _append_missing(spark, df, table, ['ticker', 'report_date'], url, driver, username, password)


def write_growth_estimates(spark: SparkSession, df: DataFrame, url: str, driver: str, username: str, password: str,
                           table: str = 'growth_estimates') -> int:
    # Primary key: (estimate_fetch_date, ticker, period)
    return _append_missing(spark, df, table, ['estimate_fetch_date', 'ticker', 'period'], url, driver, username, password)


def write_revenue_estimates(spark: SparkSession, df: DataFrame, url: str, driver: str, username: str, password: str,
                            table: str = 'revenue_estimates') -> int:
    # Primary key: (ticker, next_earnings_date, period)
    return _append_missing(spark, df, table, ['ticker', 'next_earnings_date', 'period'], url, driver, username, password)


def write_quarterly_income(spark: SparkSession, df: DataFrame, url: str, driver: str, username: str, password: str,
                           table: str = 'quarterly_income') -> int:
    return _append_missing(spark, df, table, ['ticker', 'report_date'], url, driver, username, password)


def write_stock_details(spark: SparkSession, df: DataFrame, url: str, driver: str, username: str, password: str,
                        table: str = 'stock_details') -> int:
    # Primary key likely on Symbol; if composite exists, adjust accordingly
    return _append_missing(spark, df, table, ['Symbol'], url, driver, username, password)


def pandas_to_spark(spark: SparkSession, pdf: pd.DataFrame) -> DataFrame:
    """Convert pandas DataFrame to Spark DataFrame via records.

    - Normalizes any '*date*' column to ISO 'YYYY-MM-DD' string or None
    - Ensures ticker is str
    - Replaces NaN/inf with None to avoid JDBC issues
    """
    if pdf is None or pdf.empty:
        return None

    def _sanitize_value_for_spark(value):
        # Convert numpy scalar types to native Python scalars
        if isinstance(value, np.generic):
            try:
                return value.item()
            except Exception:
                return str(value)
        # Convert numpy arrays to Python lists
        if isinstance(value, np.ndarray):
            try:
                return value.tolist()
            except Exception:
                return [str(v) for v in value]
        # Convert lists/tuples possibly containing numpy types
        if isinstance(value, (list, tuple)):
            return [
                v.item() if isinstance(v, np.generic) else (v.tolist() if isinstance(v, np.ndarray) else v)
                for v in value
            ]
        # Convert dictionaries to JSON strings to avoid complex types
        if isinstance(value, dict):
            try:
                return json.dumps(value)
            except Exception:
                return str(value)
        return value

    df = pdf.copy()
    # Normalize date-like columns to 'YYYY-MM-DD' strings or None
    for col in df.columns:
        lower = str(col).lower()
        if 'date' in lower:
            ser = pd.to_datetime(df[col], errors='coerce').dt.date
            # Convert to ISO string where available, else None
            df[col] = ser.apply(lambda d: d.isoformat() if isinstance(d, _dt.date) else None)
    # Ensure ticker is string if present
    if 'ticker' in df.columns:
        df['ticker'] = df['ticker'].astype(str)
    # Replace inf/NaN
    df.replace([np.inf, -np.inf], np.nan, inplace=True)
    df = df.where(pd.notna(df), None)
    # Sanitize any remaining numpy or complex objects cell-wise
    if hasattr(df, 'map'):
        df = df.map(_sanitize_value_for_spark)
    else:
        df = df.applymap(_sanitize_value_for_spark)
    records = df.to_dict(orient='records')
    return spark.createDataFrame(records)


