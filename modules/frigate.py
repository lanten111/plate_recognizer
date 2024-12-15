import json
import time
from datetime import datetime

import requests

from modules.config import Config
from modules.database import select_from_table
from modules.detection import get_vehicle_direction, process_plate_detection

event_type = None

def process_message(config:Config,  message, logger):

    global event_type
    payload_dict = json.loads(message.payload)
    logger.debug(f"MQTT message: {payload_dict}")

    before_data = payload_dict.get("before", {})
    after_data = payload_dict.get("after", {})
    event_type = payload_dict.get("type", "")
    frigate_event_id = after_data["id"]

    if is_invalid_event(config, after_data, logger):
        return

    if is_duplicate_event(config, frigate_event_id, logger):
        return

    config.executor.submit(get_vehicle_direction, config, after_data, frigate_event_id, logger)

    if event_type == "new":
        print(f"Starting new thread for new event{event_type} for {frigate_event_id}***************")
        config.executor.submit(begin_process, config, after_data, frigate_event_id, logger)

def begin_process(config:Config, after_data, frigate_event_id, logger):
    global event_type
    loop = 0
    while event_type in ["update", "new"] and not is_plate_found_for_event(config, frigate_event_id, logger):
        loop=loop + 1
        timestamp = datetime.now()
        print(f"{timestamp} start processing loop {loop} for {frigate_event_id}")
        config.executor.submit(process_plate_detection ,config,  after_data['camera'], frigate_event_id, logger)
        time.sleep(0.5)

    print(f"Done processing event {frigate_event_id}, {event_type}")


def is_invalid_event(config:Config, after_data, logger):

    # config_zones = config['frigate'].get('zones', [])
    config_zones = []
    config_cameras = config.camera

    matching_zone = any(value in after_data['current_zones'] for value in config_zones) if config_zones else True
    matching_camera = after_data['camera'] in config_cameras if config_cameras else True

    if not (matching_zone and matching_camera):
        logger.debug(f"Skipping event: {after_data['id']} because it does not match the configured zones/cameras")
        return True

    valid_objects = config.default_objects
    if after_data['label'] in valid_objects:
        logger.debug(f"is not a correct label: {after_data['label']}")
        return True

    return False

def is_duplicate_event(config:Config, frigate_event_id, logger):
    columns = '*'
    where = 'frigate_event_id = ?'
    params = (frigate_event_id,)
    results = select_from_table(config.db_path , config.table, columns,  where, params )
    return True if results else False

def is_plate_found_for_event(config, frigate_event_id, logger):
    columns = '*'
    where = 'frigate_event_id = ?'
    params = (frigate_event_id,)
    results = select_from_table(config.db_path , config.table, columns,  where, params )
    return True if results else False

def get_snapshot(config:Config, frigate_event_id, cropped, camera_name, logger):
    logger.debug(f"Getting snapshot for event: {frigate_event_id}, Crop: {cropped}")
    snapshot_url = f"{config.frigate_url}/api/events/{frigate_event_id}/snapshot-clean.png"
    logger.debug(f"event URL: {snapshot_url}")

    parameters = {"crop": 1 if cropped else 0, "quality": 100}
    response = requests.get(snapshot_url, params=parameters)
    snapshot = response.content
    # config.executor.submit(save_snap, snapshot, camera_name)
    if response.status_code != 200:
        logger.error(f"Error getting snapshot: {response.status_code}")
        return

    return snapshot
