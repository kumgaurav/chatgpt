import os
import configparser
from typing import Optional, Dict


def resolve_config_path() -> str:
    pyspark_root = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(pyspark_root, 'conf', 'config.ini')


def load_config(config_path: Optional[str] = None) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    path = config_path or resolve_config_path()
    if not os.path.exists(path):
        raise FileNotFoundError(f"Config file not found at {path}")
    cfg.read(path)
    return cfg


def get_mysql_settings(cfg: configparser.ConfigParser,
                       host: Optional[str] = None,
                       port: int = 3306,
                       database: Optional[str] = None,
                       url: Optional[str] = None,
                       username: Optional[str] = None,
                       password: Optional[str] = None,
                       driver: Optional[str] = None) -> Dict[str, str]:
    mysql_section = 'mysql'
    if not cfg.has_section(mysql_section):
        raise ValueError("[mysql] section missing in config.ini")

    driver_val = driver or cfg.get(mysql_section, 'driver', fallback='com.mysql.cj.jdbc.Driver')
    user_val = username or cfg.get(mysql_section, 'username', fallback='')
    pwd_val = password or cfg.get(mysql_section, 'password', fallback='')

    if url:
        jdbc_url = url
    else:
        host_val = host or cfg.get(mysql_section, 'url', fallback='localhost')
        db_val = database or cfg.get(mysql_section, 'database', fallback=None)
        if not db_val:
            raise ValueError("Database name must be provided via argument or [mysql].database in config.ini")
        jdbc_url = f"jdbc:mysql://{host_val}:{port}/{db_val}"

    return {
        'url': jdbc_url,
        'user': user_val,
        'password': pwd_val,
        'driver': driver_val,
    }


