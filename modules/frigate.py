import json
import time
from datetime import datetime

import requests

from modules.config import Config
from modules.database import select_from_table
from modules.detection import get_vehicle_direction, process_plate_detection, create_or_update_plate
from modules.mqtt.sender import send_mqtt_message

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

    logger.info(f"new message with status {event_type} for event id {frigate_event_id}")
    config.executor.submit(trigger_detected_on_zone, config, after_data, mqtt_client, logger)

    if is_duplicate_event(config, frigate_event_id, logger):
        return

    config.executor.submit(get_vehicle_direction, config, after_data, frigate_event_id, logger)

    if event_type == "new":
        logger.info(f"Starting new thread for new event{event_type} for {frigate_event_id}***************")
        config.executor.submit(begin_process, config, after_data, frigate_event_id, mqtt_client, logger)


def begin_process(config:Config, after_data, frigate_event_id, mqtt_client, logger):
    global event_type
    loop = 0
    while event_type in ["update", "new"] and not is_plate_matched_for_event(config, frigate_event_id, logger):
        loop=loop + 1
        logger.info(f"start processing loop {loop} for {frigate_event_id}")
        # config.executor.submit(process_plate_detection ,config,  after_data['camera'], frigate_event_id, mqtt_client, logger)
        process_plate_detection(config,  after_data['camera'], frigate_event_id, after_data['entered_zones'], mqtt_client, logger)
        # time.sleep(0.5)
        # time.sleep(0.5)
        logger.info(f"Done processing loop {loop}, {event_type}")
    logger.info(f"Done processing event {frigate_event_id}, {event_type}")

def trigger_detected_on_zone(config, after_data, mqtt_client, logger):
    frigate_event_id = after_data["id"]
    entered_zones = after_data['entered_zones']
    where = 'frigate_event_id = ?'
    params = (frigate_event_id,)
    results = select_from_table(config.db_path , config.table, "*",  where, params, logger)
    if len(results) > 0 and results[0].get('vehicle_detected') == 1 and results[0].get('trigger_zone_reached') != 1:
        for camera in config.camera:
            trigger_zones = config.camera.get(camera).trigger_zones
            if len(config.camera.get(camera).trigger_zones) > 0:
                if camera.lower() == results[0].get('camera_name'):
                    if set(trigger_zones) & set(entered_zones):
                        logger.info(f"trigger zone {trigger_zones} reached, sending a mqtt message({config.camera.get(camera).trigger_zones})")
                        create_or_update_plate(config, frigate_event_id, trigger_zone_reached=True, entered_zones=json.dumps(entered_zones), logger=logger)
                        # update_plate_db_zones_status(config, frigate_event_id, True, json.dumps(entered_zones), logger)
                        send_mqtt_message(config , results[0], mqtt_client, logger)
                    else:
                        logger.info(f"trigger zone {trigger_zones} not reached, current reached {entered_zones}")
                else:
                    logger.info(f"current camera does not match {camera} not reached, current reached {results[0].get('camera_name')}")
            else:
                logger.info(f"trigger zones empty, skipping")
    else:
        logger.info(f"No entry in db for event {frigate_event_id} or plate not matched")


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
    if results and results[0]['matched'] is None:
        return False
    elif results and results[0]['matched'] is not None:
        return True
    elif not results:
        return False

def is_plate_matched_for_event(config, frigate_event_id, logger):
    columns = '*'
    where = 'frigate_event_id = ?'
    params = (frigate_event_id,)
    results = select_from_table(config.db_path , config.table, columns,  where, params, logger )
    if results and results[0]['matched'] is None:
        return False
    elif results and results[0]['matched'] is not None:
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
