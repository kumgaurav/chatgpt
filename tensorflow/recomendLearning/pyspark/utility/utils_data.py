from typing import List, Tuple
import configparser


def get_ticker_list(cfg: configparser.ConfigParser,
                    spark,
                    jdbc_url: str,
                    driver: str,
                    username: str,
                    password: str,
                    stock_change_tracker_table: str) -> Tuple[List[str], str]:
    """Resolve list of tickers from config or DB.

    Returns (ticker_list, source) where source is 'config' or 'db'.
    """
    symbols_value = cfg.get('stocks', 'symbols', fallback='')
    symbols_value = symbols_value.strip() if symbols_value else ''
    if symbols_value:
        return symbols_value.split(), 'config'

    change_tracker_df = spark.read.format("jdbc").options(
        url=jdbc_url,
        driver=driver,
        dbtable=stock_change_tracker_table,
        user=username,
        password=password
    ).load()

    change_tracker_df = change_tracker_df.filter(change_tracker_df.is_active == True).select("symbol")
    ticker_list = change_tracker_df.rdd.flatMap(lambda x: x).collect()
    return ticker_list, 'db'


