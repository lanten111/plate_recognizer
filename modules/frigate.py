import json
import time
from datetime import datetime

import requests

from modules.config import Config
from modules.database import select_from_table
from modules.detection import get_vehicle_direction, process_plate_detection

event_type = None

def process_message(config:Config, message, mqtt_client, logger):

    global event_type
    payload_dict = json.loads(message.payload)
    logger.debug(f"MQTT message: {payload_dict}")

    before_data = payload_dict.get("before", {})
    after_data = payload_dict.get("after", {})
    event_type = payload_dict.get("type", "")
    frigate_event_id = after_data["id"]

    #add trigger for detectied

    if is_invalid_event(config, after_data, logger):
        return

    if is_duplicate_event(config, frigate_event_id, logger):
        return


    # trigger_detected_on_zone(config, after_data, mqtt_client, logger)

    config.executor.submit(get_vehicle_direction, config, after_data, frigate_event_id, logger)
    # get_vehicle_direction(config, after_data, frigate_event_id, logger)

    if event_type == "new":
        logger.info(f"Starting new thread for new event{event_type} for {frigate_event_id}***************")
        config.executor.submit(begin_process, config, after_data, frigate_event_id, mqtt_client, logger)

def trigger_detected_on_zone(config, after_data, mqtt_client, logger):
        results = select_from_table(config.db_path , config.table, logger=logger )
        if results and results[0].get('plate_found') == 1:
            if len(after_data['current_zones']) > 0:
                    current_zone = after_data['current_zones'][0]
                    cameras = config.camera
                    for camera in cameras:
                        if len(config.camera.get(camera).trigger_zones) > 0:
                            if camera.lower() == results[0].get('camera_name'):
                                if current_zone in config.camera.get('camera').trigger_zones:
                                    state_topic = f"homeassistant/binary_sensor/vehicle_data/matched/state"
                                    mqtt_client.publish(state_topic, True , retain=True)
                                    time.sleep(10)
                                    mqtt_client.publish(state_topic, False, retain=True)


def begin_process(config:Config, after_data, frigate_event_id, mqtt_client, logger):
    global event_type
    loop = 0
    while event_type in ["update", "new"] and not is_plate_found_for_event(config, frigate_event_id, logger):
        loop=loop + 1
        logger.info(f"start processing loop {loop} for {frigate_event_id}")
        # config.executor.submit(process_plate_detection ,config,  after_data['camera'], frigate_event_id, mqtt_client, logger)
        process_plate_detection(config,  after_data['camera'], frigate_event_id, mqtt_client, logger)
        # time.sleep(0.5)
        logger.info(f"Done processing loop {loop}, {event_type}")
    logger.info(f"Done processing event {frigate_event_id}, {event_type}")


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
    if after_data['label'] not in valid_objects:
        logger.debug(f"is not a correct label: {after_data['label']}")
        return True

    return False

def is_duplicate_event(config:Config, frigate_event_id, logger):
    columns = '*'
    where = 'frigate_event_id = ?'
    params = (frigate_event_id,)
    results = select_from_table(config.db_path , config.table, columns,  where, params, logger )
    if results and results[0]['plate_found'] is None:
        return False
    elif results and results[0]['plate_found'] is not None:
        return True
    elif not results:
        return False

def is_plate_found_for_event(config, frigate_event_id, logger):
    columns = '*'
    where = 'frigate_event_id = ?'
    params = (frigate_event_id,)
    results = select_from_table(config.db_path , config.table, columns,  where, params, logger )
    if results and results[0]['plate_found'] is None:
        return False
    elif results and results[0]['plate_found'] is not None:
        return True
    elif not results:
        return False

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
