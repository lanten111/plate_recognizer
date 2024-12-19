import difflib
import os
import time
import uuid
from copy import Error
from datetime import datetime

import cv2
import numpy as np
import requests

from modules.logger import setup_logger
from modules.alpr import get_alpr, fast_alpr
from modules.config import Config
from modules.database import create_or_update_plate, get_plate
from modules.mqtt.sender import send_mqtt_message

event_type = None

logger = setup_logger(__name__)

def process_plate_detection(config:Config, camera_name, frigate_event_id, entered_zones, mqtt_client):
    try:
        logger.info(f"start processing event {frigate_event_id}")
        snapshot = get_latest_snapshot(config, frigate_event_id, camera_name)
        results = get_plate(config, frigate_event_id)
        # config.executor(save_image, config, config.debug_snapshot_path,  None, snapshot, camera_name, None, frigate_event_id, logger)

        if len(results) == 0 or (len(results) > 0 and (results[0].get('is_watched_plate_matched') is not None or bool(results[0].get('is_watched_plate_matched'))) is False) :
            detected_plate, detected_plate_score = do_plate_detection(config, snapshot)
            matched_watched_plate, fuzzy_score, watched_plates = check_watched_plates(config, detected_plate)

            if (matched_watched_plate is not None and fuzzy_score is not None) or config.fuzzy_match is None:
                trigger_zones = config.camera.get(camera_name).trigger_zones
                image_path = save_image(config, config.snapshot_path,  detected_plate_score, snapshot, camera_name, detected_plate, frigate_event_id)
                logger.info(f"db storing plate or updating plate detection for event {frigate_event_id}")
                create_or_update_plate(config, frigate_event_id, camera_name=camera_name,  detected_plate=detected_plate,
                                                 matched_watched_plate=matched_watched_plate, watched_plates=watched_plates, detection_time=datetime.now(),
                                                 fuzzy_score=fuzzy_score, is_watched_plate_matched=True, is_trigger_zone_reached=False, trigger_zones=trigger_zones ,
                                                 entered_zones=entered_zones, image_path=image_path)
                #wait for trigger zone to trigger the on condition
                if len(config.camera.get(camera_name).trigger_zones) > 0:
                    if set(config.camera.get(camera_name).trigger_zones) & set(entered_zones):
                            logger.info(f"trigger zone {entered_zones} reached, sending a mqtt message({config.camera.get(camera_name).trigger_zones})")
                            logger.info(f"db storing plate or updating plate detection for event {frigate_event_id}")
                            create_or_update_plate(config, frigate_event_id, is_trigger_zone_reached=True, entered_zones=entered_zones)
                            send_mqtt_message(config , frigate_event_id,  mqtt_client)
                    else:
                        logger.info(f"trigger zone ({config.camera.get(camera_name).trigger_zones}) waiting for trigger zone ")
                        send_mqtt_message(config , frigate_event_id,  mqtt_client)
                else:
                        logger.info(f"no trigger zone present, sending mqtt message({config.camera.get(camera_name).trigger_zones})")
                        send_mqtt_message(config , frigate_event_id , mqtt_client)
                config.executor.submit(delete_old_images, config.days_to_keep_images_in_days, config.debug_snapshot_path)
                config.executor.submit(delete_old_images, config.days_to_keep_images_in_days, config.snapshot_path)
                logger.info(f"plate({detected_plate}) match found in watched plates ({matched_watched_plate}) for event {frigate_event_id}, {event_type} stops")
        else:
            logger.info(f"plate already found for event {frigate_event_id}, {event_type} skipping........")
    except Exception as e:
        logger.error(f"Something went wrong processing event: {e}")
        raise Error(e)

def get_latest_snapshot(config:Config, frigate_event_id, camera_name):
    logger.info(f"Getting snapshot for event: {frigate_event_id}")
    snapshot_url = f"{config.frigate_url}/api/{camera_name}/latest.jpg"
    logger.debug(f"event URL: {snapshot_url}")
    parameters = {"quality": 100}
    response = requests.get(snapshot_url, params=parameters)
    snapshot = response.content
    return snapshot

def get_vehicle_direction(config:Config,  after_data, frigate_event_id):
    results = get_plate(config, frigate_event_id)
    if len(after_data['current_zones']) > 0:
        if len(results) == 0 or (len(results) > 0 and results[0].get('vehicle_detected') =='unknown' or
                                 results['vehicle_detected'] is None):
            current_zone = after_data['current_zones'][0]
            for camera in config.camera:
                if camera == after_data['camera']:
                    if config.camera.get(camera).direction.first_zone and config.camera.get(camera).direction.last_zone:
                        if current_zone.lower() == config.camera.get(camera).direction.first_zone.lower():
                            vehicle_direction  = 'entering'
                        elif current_zone.lower() == config.camera.get(camera).direction.last_zone.lower():
                            vehicle_direction  = 'exiting'
                        else:
                            vehicle_direction = 'unknown'
                        logger.info(f"db storing plate or updating vehicle direction  for event {frigate_event_id}")
                        create_or_update_plate(config , frigate_event_id, vehicle_direction=vehicle_direction)
                    else:
                        logger.info(f"skipping vehicle direction direction for {frigate_event_id}, missing first_zone and or last_zone in config.")
        else:
            logger.info(f"event  {frigate_event_id} vehicle direction exit as.")
    else:
        logger.info(f"skipping direction detection for event  {frigate_event_id} does not contain zone, ")

def check_watched_plates(config:Config, detected_plate):
    config_watched_plates = config.watched_plates
    if not config_watched_plates:
        logger.info("Skipping checking Watched Plates because watched_plates is not set")
        return None, None, None

    if not detected_plate:
        logger.info("Skipping checking Watched Plates because no plate detected")
        return None, None, None

    if config.fuzzy_match == 0:
        logger.info("Skipping fuzzy matching because fuzzy_match value not set in config")
        return None, None, None

    best_match, fuzzy_score = None, 0
    watched_plates = []
    for watched_plate in config_watched_plates:
        seq = difflib.SequenceMatcher(a=str(detected_plate).lower(), b=str(watched_plate.number).lower())
        if seq.ratio() > fuzzy_score:
            fuzzy_score = round(seq.ratio(), 2)
            best_match = watched_plate
    if fuzzy_score > config.fuzzy_match:
        logger.info(f"Best fuzzy_match: {best_match} ({fuzzy_score})")
        return best_match, fuzzy_score, config_watched_plates
    else:
        logger.info(f"Fuzzy match too low for : {best_match} with score ({fuzzy_score})")
        return None, None, None


def save_image(config:Config, snapshot_path,  plate_score, snapshot, camera_name, plate_number, frigate_id):
    timestamp = datetime.now().strftime(config.date_format)
    image_name = f"{camera_name}__{uuid.uuid4()}_{timestamp}.png"
    logger.info(f"{datetime.now()} saving  plate({plate_number}) image {image_name}")
    if plate_number:
        image_name = f"{str(plate_number).upper()}_{int(plate_score* 100)}%_{image_name}"
    else:
        image_name = f"{frigate_id}_{image_name}"
    image_path = f"{snapshot_path}/{image_name}"

    image_array = np.frombuffer(snapshot, np.uint8)
    frame = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    annotated_frame = get_alpr(config).draw_predictions(frame)
    cv2.imwrite(image_path, annotated_frame)

    logger.info(f"successfully saved image with path: {image_path}")
    return image_path

# def save_snap(config, snapshot, frigate_id, camera_name, logger):
#     timestamp = datetime.now().strftime(config.date_format)
#     image_name = f"{camera_name}_{frigate_id}_{timestamp}_{uuid.uuid4()}.png"
#     image_path = f"{config.debug_snapshot_path}/{image_name}"
#     with open(image_path, "wb") as file:
#         file.write(snapshot)
#         logger.debug(f"{timestamp} saved snapshot {image_path}")


def do_plate_detection(config:Config, snapshot):
    if config.fast_alpr:
        detected_plate_number, detected_plate_score = fast_alpr(config, snapshot)
    else:
        logger.error("Plate Recognizer is not configured")
        return None, None, None, None

    return detected_plate_number, detected_plate_score

def delete_old_images(days_to_keep_images_in_days, path):

    now = time.time()
    cutoff = now - (days_to_keep_images_in_days * 86400)  # 86400 seconds in a day

    for filename in os.listdir(path):
        file_path = os.path.join(path, filename)

        if os.path.isfile(file_path):
            file_mtime = os.path.getmtime(file_path)
            if file_mtime < cutoff:
                try:
                    os.remove(file_path)
                    logger.debug(f"Deleted: {file_path}")
                except Exception as e:
                    logger.debug(f"Failed to delete {file_path}: {e}")