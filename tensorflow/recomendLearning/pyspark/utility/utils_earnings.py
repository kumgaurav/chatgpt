from typing import List
import configparser
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.functions import col, current_date


def get_tickers_needing_earnings_update(
    cfg: configparser.ConfigParser,
    spark: SparkSession,
    jdbc_url: str,
    driver: str,
    username: str,
    password: str,
    input_tickers: List[str],
    earnings_estimates_table: str = 'earnings_estimates',
) -> List[str]:
    """
    Return tickers whose earnings date data is missing or stale.

    Criteria:
    - Missing: no row exists in `earnings_estimates_table` for the ticker
    - Stale: the latest `earnings_date` recorded is less than today

    Args:
        cfg: Loaded configuration (not required here but kept for symmetry).
        spark: SparkSession instance.
        jdbc_url: JDBC URL for MySQL.
        driver: JDBC driver class name.
        username: DB username.
        password: DB password.
        input_tickers: Candidate tickers to evaluate.
        earnings_estimates_table: Table name to query (default: 'earnings_estimates').

    Returns:
        A list of tickers requiring an update.
    """
    if not input_tickers:
        return []

    # Read only the latest earnings_date per ticker using a JDBC subquery to reduce data transfer
    subquery = f"(SELECT ticker, MAX(earnings_date) AS last_date FROM {earnings_estimates_table} GROUP BY ticker) latest"
    latest_df: DataFrame = (
        spark.read.format('jdbc')
        .options(
            url=jdbc_url,
            driver=driver,
            dbtable=subquery,
            user=username,
            password=password,
        )
        .load()
    )

    # Build a DataFrame from the input tickers and left join to the latest dates
    tickers_df = spark.createDataFrame([(t,) for t in input_tickers], ['ticker'])
    joined = tickers_df.join(latest_df, on='ticker', how='left')

    # Missing (last_date is null) OR stale (last_date < today)
    needing_update = joined.filter((col('last_date').isNull()) | (col('last_date') < current_date()))

    result_df = needing_update.select('ticker').distinct()
    print(f"🎯 Tickers needing earnings update: {result_df.count()}")
    return [row['ticker'] for row in result_df.collect()]


