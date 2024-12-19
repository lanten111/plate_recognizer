import os

import sys

from logger import setup_logger
from modules.config import get_yaml_config
from modules.database import setup_db
from modules.mqtt.receiver import initiate_mqtt_receiver

VERSION = '2.1.1'

logger = setup_logger(__name__)

def main():

    config = get_yaml_config()

    setup_db(config)

    os.makedirs(config.snapshot_path, exist_ok=True)
    os.makedirs(config.debug_snapshot_path, exist_ok=True)

    logger.info(f"Python Version: {sys.version}")
    logger.info(f"Frigate Plate Recognizer Version: {VERSION}")
    logger.debug(f"config: {config}")

    initiate_mqtt_receiver(config)


if __name__ == '__main__':
    main()


