import argparse
import os
import sys
import re
import configparser
from typing import Optional

from pyspark.sql import SparkSession
from pyspark.sql import functions as F


def resolve_config_path() -> str:
    """
    Resolve path to pyspark/conf/config.ini regardless of this file's subdirectory.
    """
    pyspark_root = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(pyspark_root, 'conf', 'config.ini')


def load_config(config_path: Optional[str]) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    path = config_path or resolve_config_path()
    if not os.path.exists(path):
        raise FileNotFoundError(f"Config file not found at {path}")
    cfg.read(path)
    return cfg


def get_mysql_settings(cfg: configparser.ConfigParser,
                       host: Optional[str],
                       port: int,
                       database: Optional[str],
                       url: Optional[str],
                       username: Optional[str],
                       password: Optional[str],
                       driver: Optional[str]) -> dict:
    mysql_section = 'mysql'
    if not cfg.has_section(mysql_section):
        raise ValueError("[mysql] section missing in config.ini")

    driver_val = driver or cfg.get(mysql_section, 'driver', fallback='com.mysql.cj.jdbc.Driver')
    user_val = username or cfg.get(mysql_section, 'username', fallback=None)
    pwd_val = password or cfg.get(mysql_section, 'password', fallback=None)

    if url:
        jdbc_url = url
    else:
        host_val = host or cfg.get(mysql_section, 'url', fallback='localhost')
        db_val = database or cfg.get(mysql_section, 'database', fallback=None)
        if not db_val:
            raise ValueError("Database name must be provided via --database or [mysql].database in config.ini")
        jdbc_url = f"jdbc:mysql://{host_val}:{port}/{db_val}"

    return {
        'url': jdbc_url,
        'user': user_val,
        'password': pwd_val,
        'driver': driver_val,
    }


def build_spark(app_name: str, cfg: configparser.ConfigParser) -> SparkSession:
    # Keep Python executable consistent across driver/executors
    python_path = sys.executable
    os.environ['PYSPARK_PYTHON'] = python_path
    os.environ['PYSPARK_DRIVER_PYTHON'] = python_path

    mysql_jar = cfg.get('spark', 'mysql_jar_path', fallback='')
    builder = SparkSession.builder.appName(app_name).master('local[*]')
    if mysql_jar and os.path.exists(mysql_jar):
        builder = builder.config('spark.jars', mysql_jar)
    return builder.getOrCreate()


def normalize_column_name(name: str) -> str:
    # Lowercase, replace non-alphanumeric with underscore, collapse repeats, trim underscores
    lowered = name.strip().lower()
    sanitized = re.sub(r'[^0-9a-zA-Z]+', '_', lowered)
    sanitized = re.sub(r'_+', '_', sanitized).strip('_')
    return sanitized or 'col'


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Load a CSV into a MySQL table using Spark JDBC')
    p.add_argument('--csv', required=True, help='Path to input CSV file')
    p.add_argument('--table', required=True, help='Target MySQL table name')
    p.add_argument('--config', help='Path to config.ini (defaults to pyspark/conf/config.ini)')

    # CSV read options
    p.add_argument('--delimiter', default=',', help='CSV delimiter (default: ,)')
    p.add_argument('--header', type=lambda v: str(v).lower() in ('1', 'true', 'yes', 'y'), default=True,
                   help='CSV has header row (default: true)')
    p.add_argument('--infer-schema', dest='infer_schema', type=lambda v: str(v).lower() in ('1','true','yes','y'),
                   default=True, help='Infer schema (default: true)')
    p.add_argument('--quote', default='"', help='Quote character (default: ")')
    p.add_argument('--escape', default='\\', help='Escape character (default: \\)')
    p.add_argument('--null-value', dest='null_value', default='\\N', help='Null value marker (default: \\N)')
    p.add_argument('--encoding', default='UTF-8', help='File encoding (default: UTF-8)')
    p.add_argument('--multi-line', dest='multi_line', type=lambda v: str(v).lower() in ('1','true','yes','y'),
                   default=False, help='Enable multiLine CSV parsing (default: false)')

    # Transformations
    p.add_argument('--normalize-cols', dest='normalize_cols', action='store_true',
                   help='Normalize column names to lowercase_snake_case (safe for MySQL)')
    p.add_argument('--repartition', type=int, default=0,
                   help='Repartition DataFrame before write (0=skip)')

    # JDBC write options
    p.add_argument('--mode', choices=['append', 'overwrite', 'ignore', 'error', 'errorifexists'], default='append',
                   help='Save mode for JDBC write (default: append)')
    p.add_argument('--batchsize', type=int, default=5000, help='JDBC batch size (default: 5000)')
    p.add_argument('--truncate', type=lambda v: str(v).lower() in ('1','true','yes','y'), default=False,
                   help='Use truncate when mode=overwrite (default: false)')
    p.add_argument('--create-table-column-types', dest='create_types', default=None,
                   help='MySQL column types for table creation, e.g. "id INT, name VARCHAR(255)"')

    # MySQL connection overrides
    p.add_argument('--url', help='Full JDBC URL (overrides host/port/database), e.g. jdbc:mysql://host:3306/db')
    p.add_argument('--host', help='MySQL host (default from config [mysql].url)')
    p.add_argument('--port', type=int, default=3306, help='MySQL port (default: 3306)')
    p.add_argument('--database', help='MySQL database name (default from config)')
    p.add_argument('--username', help='MySQL username (default from config)')
    p.add_argument('--password', help='MySQL password (default from config)')
    p.add_argument('--driver', help='JDBC driver class (default from config or com.mysql.cj.jdbc.Driver)')

    return p.parse_args()


def main():
    args = parse_args()

    cfg = load_config(args.config)
    mysql = get_mysql_settings(
        cfg,
        host=args.host,
        port=args.port,
        database=args.database,
        url=args.url,
        username=args.username,
        password=args.password,
        driver=args.driver,
    )

    spark = build_spark('CSV to MySQL Loader', cfg)

    read_options = {
        'header': str(bool(args.header)).lower(),
        'inferSchema': str(bool(args.infer_schema)).lower(),
        'delimiter': args.delimiter,
        'quote': args.quote,
        'escape': args.escape,
        'nullValue': args.null_value,
        'encoding': args.encoding,
        'multiLine': str(bool(args.multi_line)).lower(),
    }

    if not os.path.exists(args.csv):
        raise FileNotFoundError(f"CSV file not found: {args.csv}")

    df = spark.read.options(**read_options).csv(args.csv)

    if args.normalize_cols:
        renamed = [F.col(c).alias(normalize_column_name(c)) for c in df.columns]
        df = df.select(*renamed)

    if args.repartition and args.repartition > 0:
        df = df.repartition(args.repartition)

    write_options = {
        'url': mysql['url'],
        'driver': mysql['driver'],
        'dbtable': args.table,
        'user': mysql['user'] or '',
        'password': mysql['password'] or '',
        'batchsize': str(args.batchsize),
    }
    if args.truncate:
        write_options['truncate'] = 'true'
    if args.create_types:
        write_options['createTableColumnTypes'] = args.create_types

    mode = 'errorifexists' if args.mode in ('error', 'errorifexists') else args.mode

    df.write.format('jdbc').options(**write_options).mode(mode).save()

    spark.stop()


if __name__ == '__main__':
    main()


