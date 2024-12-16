import os
import logging
from datetime import datetime
from logging import Logger

import sys

from modules.config import get_yaml_config
from modules.database import setup_db
from modules.mqtt import run_mqtt_client

VERSION = '2.1.1'

def load_logger(config) -> Logger:
    logger = logging.getLogger(__name__)
    logger.setLevel(config.logger_level)
    # Create a formatter to customize the log message format
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Create a console handler and set the level to display all messages
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formatter)

    # Create a file handler to log messages to a file
    file_handler = logging.FileHandler(config.log_file_path)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    # Add the handlers to the logger
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    return  logger

def main():

    config = get_yaml_config()

    setup_db(config)
    logger = load_logger(config)

    os.makedirs(config.snapshot_path, exist_ok=True)
    os.makedirs(config.debug_snapshot_path, exist_ok=True)

    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    logger.info(f"Time: {current_time}")
    logger.info(f"Python Version: {sys.version}")
    logger.info(f"Frigate Plate Recognizer Version: {VERSION}")
    logger.debug(f"config: {config}")

    run_mqtt_client(config, logger)


if __name__ == '__main__':
    main()


