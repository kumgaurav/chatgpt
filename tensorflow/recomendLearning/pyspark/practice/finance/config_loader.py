import os
import configparser


# Function to load MySQL connection details and other parameters from config file
def load_config():
    # Get the current directory and construct the path to the config file
    current_dir = os.getcwd()
    pyspark_path = current_dir.split('pyspark')[0] + 'pyspark'
    config_path = os.path.join(pyspark_path, 'conf', 'config.ini')

    # Initialize configparser and check if config file exists
    config = configparser.ConfigParser()
    if os.path.exists(config_path):
        config.read(config_path)
    else:
        raise FileNotFoundError(f"Config file not found at {config_path}")

    # Fetch and return MySQL connection details along with other parameters
    try:
        url = 'jdbc:mysql://localhost/{}'.format(config.get('mysql', 'database'))
        driver = config.get('mysql', 'driver')
        username = config.get('mysql', 'username')
        password = config.get('mysql', 'password')
        # tablename = config.get('mysql', 'tablename')  # New parameter

        # Add any additional parameters you need here, for example:
        # some_other_param = config.get('mysql', 'some_other_param')  # Another example

        return url, driver, username, password
    except configparser.NoSectionError as e:
        raise Exception(f"Missing section in config: {e}")
    except configparser.NoOptionError as e:
        raise Exception(f"Missing option in config: {e}")
