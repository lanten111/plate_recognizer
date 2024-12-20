import json

import requests

from modules.logger import setup_logger
from modules.config import Config
from modules.database import get_plate
from modules.detection import get_vehicle_direction, process_plate_detection, create_or_update_plate
from modules.mqtt.sender import send_mqtt_message

event_type = None

logger = setup_logger(__name__)

def process_message(config:Config, message, mqtt_client):

    global event_type
    payload_dict = json.loads(message.payload)
    # logger.debug(f"MQTT message: {payload_dict}")

    before_data = payload_dict.get("before", {})
    after_data = payload_dict.get("after", {})
    event_type = payload_dict.get("type", "")
    frigate_event_id = after_data["id"]

    #add trigger for detectied

    if is_invalid_event(config, after_data):
        return

    logger.info(f"new message with status {event_type} for event id {frigate_event_id}")
    config.executor.submit(trigger_detected_on_zone, config, after_data, mqtt_client)

    if is_duplicate_event(config, frigate_event_id):
        return

    config.executor.submit(get_vehicle_direction, config, after_data, frigate_event_id)

    if event_type == "new":
        logger.info(f"Starting new thread for new event{event_type} for {frigate_event_id}***************")
        config.executor.submit(begin_process, config, after_data, frigate_event_id, mqtt_client)


def begin_process(config:Config, after_data, frigate_event_id, mqtt_client):
    global event_type
    loop = 0
    while event_type in ["update", "new"] and not is_plate_matched_for_event(config, frigate_event_id):
        loop=loop + 1
        logger.info(f"start processing loop {loop} for {frigate_event_id}")
        # config.executor.submit(process_plate_detection ,config,  after_data['camera'], frigate_event_id, after_data['entered_zones'], mqtt_client, logger)
        process_plate_detection(config,  after_data['camera'], frigate_event_id, after_data['entered_zones'], mqtt_client)
        logger.info(f"Done processing loop {loop}, {event_type}")
        # time.sleep(0.2)
    logger.info(f"Done processing event {frigate_event_id}, {event_type}")

def trigger_detected_on_zone(config, after_data, mqtt_client):
    frigate_event_id = after_data["id"]
    entered_zones = after_data['entered_zones']
    results = get_plate(config, frigate_event_id)
    # remove is_watched_plate_matched in case a zone is reached before is_trigger_zone_reached
    if len(results) == 0 or (len(results) > 0 and results[0].get('is_trigger_zone_reached') != 1 or results[0].get('is_trigger_zone_reached') is None):
        for camera in config.camera:
            trigger_zones = config.camera.get(camera).trigger_zones
            if len(config.camera.get(camera).trigger_zones) > 0:
                if camera.lower() == results[0].get('camera_name').lower():
                    if set(trigger_zones) & set(entered_zones):
                        create_or_update_plate(config, frigate_event_id, is_trigger_zone_reached=True, entered_zones=entered_zones)
                        logger.info(f"trigger zone ({config.camera.get(camera).trigger_zones}) reached, current zones {entered_zones}")
                        send_mqtt_message(config , frigate_event_id , mqtt_client)
                    else:
                        logger.info(f"trigger zone {trigger_zones} not reached, current reached {entered_zones}")
                else:
                    logger.info(f"current camera does not match {camera}, current reached {results[0].get('camera_name')}")
            else:
                logger.info("trigger zones empty, skipping trigger zone detection")
    else:
        logger.info(f"trigger zone status already {results[0].get('is_trigger_zone_reached')} for event {frigate_event_id}")


def is_invalid_event(config:Config, after_data):

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

def is_duplicate_event(config:Config, frigate_event_id):
    results = get_plate(config, frigate_event_id)
    if results and results[0]['is_watched_plate_matched'] is None:
        return False
    elif results and results[0]['is_watched_plate_matched'] is not None:
        return True
    elif not results:
        return False

def is_plate_matched_for_event(config, frigate_event_id):
    results = get_plate(config, frigate_event_id)
    if results and results[0]['is_watched_plate_matched'] is None:
        return False
    elif results and results[0]['is_watched_plate_matched'] is not None:
        return True
    elif not results:
        return False

def get_snapshot(config:Config, frigate_event_id, cropped, camera_name):
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
