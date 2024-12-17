import base64
import difflib
import json
import os
import time
import uuid
from dataclasses import asdict
from datetime import datetime

import cv2
import numpy as np
import requests

from modules.alpr import get_alpr, fast_alpr
from modules.config import Config
from modules.database import insert_into_table, select_from_table, update_table, create_or_update_plate
from modules.mqtt.sender import send_mqtt_message

event_type = None

def process_plate_detection(config:Config, camera_name, frigate_event_id, entered_zones, mqtt_client, logger):
    logger.info(f"start processing event {frigate_event_id}")
    snapshot = get_latest_snapshot(config, frigate_event_id, camera_name, logger)
    # config.executor(save_image, config, config.debug_snapshot_path,  None, snapshot, camera_name, None, frigate_event_id, logger)

    if not is_plate_found_for_event(config, frigate_event_id, logger):
        detected_plate, detected_plate_score = get_plate(config, snapshot, logger)
        watched_plate, fuzzy_score, matched_plate = check_watched_plates(config, detected_plate, logger)

        if (watched_plate is not None and fuzzy_score is not None) or config.fuzzy_match is None:
            trigger_zones = config.camera.get(camera_name).trigger_zones
            image_path = save_image(config, config.snapshot_path,  detected_plate_score, snapshot, camera_name, detected_plate, frigate_event_id, logger)
            payload = create_or_update_plate(config, frigate_event_id, camera_name=camera_name,  detected_plate=detected_plate,
                                             matched_plate=matched_plate, detection_time=datetime.now(), fuzzy_score=round(fuzzy_score, 2),
                                            vehicle_detected=True, trigger_zone_reached=False, trigger_zones=trigger_zones ,
                                             entered_zones=entered_zones, image_path=image_path, logger=logger)
            #wait for trigger zone to trigger the on condition
            if len(config.camera.get(camera_name).trigger_zones) > 0:
                logger.info(f"trigger zone ({config.camera.get(camera_name).trigger_zones}) detected, waiting for zones to be reached")
                if set(config.camera.get(camera_name).trigger_zones) & set(entered_zones):
                    logger.info(f"trigger zone {entered_zones} reached, sending a mqtt message({config.camera.get(camera_name).trigger_zones})")
                    send_mqtt_message(config , payload,  mqtt_client, logger)
            else:
                logger.info(f"no trigger zone present, sending mqtt message({config.camera.get(camera_name).trigger_zones})")
                send_mqtt_message(config , payload , mqtt_client, logger)
            config.executor.submit(delete_old_images, config.days_to_keep_images_in_days, config.debug_snapshot_path, logger)
            config.executor.submit(delete_old_images, config.days_to_keep_images_in_days, config.snapshot_path, logger)
            logger.info(f"plate({detected_plate}) match found in watched plates ({watched_plate}) for event {frigate_event_id}, {event_type} stops")
    else:
        logger.info(f"plate already found for event {frigate_event_id}, {event_type} skipping........")

def get_latest_snapshot(config:Config, frigate_event_id, camera_name, logger):
    logger.info(f"Getting snapshot for event: {frigate_event_id}")
    snapshot_url = f"{config.frigate_url}/api/{camera_name}/latest.jpg"
    logger.debug(f"event URL: {snapshot_url}")
    parameters = {"quality": 100}
    response = requests.get(snapshot_url, params=parameters)
    snapshot = response.content
    return snapshot

# def add_update_plate_db(config, frigate_event_id, camera_name,  detected_plate, matched_plate, detection_time,fuzzy_score, vehicle_detected, trigger_zone_reached, trigger_zones, entered_zones, image_path, logger):
#     logger.info(f"storing plate({detected_plate}) in db for event {frigate_event_id}")
#     columns = '*'
#     where = 'frigate_event_id = ?'
#     params = (frigate_event_id,)
#     results = select_from_table(config.db_path, config.table, columns, where, params, logger)
#     if results:
#         set_clause = 'detection_time = ?, fuzzy_score = ?, detected_plate = ? , camera_name = ?, matched_plate = ? , watched_plates = ?, vehicle_detected = ?, trigger_zone_reached = ?, trigger_zones = ?, entered_zones = ?, vehicle_owner = ?, vehicle_brand = ?, image_path = ?'
#         where = 'frigate_event_id = ?'
#         params = (detection_time, fuzzy_score, detected_plate, camera_name, matched_plate.number, json.dumps(config.watched_plates), vehicle_detected, trigger_zone_reached,  json.dumps(trigger_zones), json.dumps(trigger_zones), matched_plate.owner, matched_plate.car_brand, image_path, frigate_event_id)
#         results =  update_table(config.db_path, config.table, set_clause, where, params, logger)
#         logger.info(f"updated db for event  {frigate_event_id}.")
#     else:
#         columns = ('detection_time', 'fuzzy_score', 'detected_plate', 'frigate_event_id', 'camera_name','matched_plate', 'watched_plates', 'vehicle_detected', 'trigger_zone_reached', 'trigger_zones', 'entered_zones', 'vehicle_owner', 'vehicle_brand', 'image_path')
#         values = (detection_time, fuzzy_score, detected_plate, frigate_event_id, camera_name, matched_plate.number , json.dumps(config.watched_plates),  vehicle_detected, trigger_zone_reached, json.dumps(trigger_zones) , json.dumps(entered_zones), matched_plate.owner, matched_plate.car_brand, image_path)
#         results = insert_into_table(config.db_path, config.table, columns, values, logger)
#         logger.info(f"inserted db for event  {frigate_event_id}.")
#     return results[0]



# def update_plate_db_zones_status(config, frigate_event_id, trigger_zone_reached, entered_zones, logger):
#     set_clause = 'trigger_zone_reached = ? , entered_zones = ?'
#     where = 'frigate_event_id = ?'
#     params = (trigger_zone_reached,  entered_zones, frigate_event_id)
#     update_table(config.db_path, config.table, set_clause, where, params, logger)
#     logger.info(f"updated db for event  {frigate_event_id}.")

def get_vehicle_direction(config:Config,  after_data, frigate_event_id, logger):
    direction = get_db_event_direction(config, frigate_event_id, logger)
    if len(after_data['current_zones']) > 0:
        if len(direction) == 0:
            current_zone = after_data['current_zones'][0]
            cameras = config.camera
            for camera in cameras:
                if camera == after_data['camera']:
                    if config.camera.get(camera).direction.first_zone and config.camera.get(camera).direction.last_zone:
                        if current_zone.lower() == config.camera.get(camera).direction.first_zone.lower():
                            vehicle_direction  = 'entering'
                        elif current_zone.lower() == config.camera.get(camera).direction.last_zone.lower():
                            vehicle_direction  = 'exiting'
                        else:
                            vehicle_direction = 'unknown'
                        columns = ('frigate_event_id', 'vehicle_direction')
                        create_or_update_plate(config , frigate_event_id, vehicle_direction=vehicle_direction, logger=logger)
                        # insert_into_table(config.db_path, config.table, columns, values, logger)
                    else:
                        logger.info(f"skipping vehicle direction direction for {frigate_event_id}, missing first_zone and or last_zone in config.")
        else:
            logger.info(f"event  {frigate_event_id} vehicle direction exit as {direction}.")
    else:
        logger.info(f"skipping direction detection for event  {frigate_event_id} does not contain zone, ")

def check_watched_plates(config:Config, detected_plate, logger):
    config_watched_plates = config.watched_plates
    if not config_watched_plates:
        logger.info("Skipping checking Watched Plates because watched_plates is not set")
        return None, None, None

    if not detected_plate:
        logger.info("Skipping checking Watched Plates because no plate detected")
        return None, None, None

    if config.fuzzy_match == 0:
        logger.info(f"Skipping fuzzy matching because fuzzy_match value not set in config")
        return None, None, None

    best_match, max_score = None, 0
    for watched_plate in config_watched_plates:
        seq = difflib.SequenceMatcher(a=str(detected_plate).lower(), b=str(watched_plate.number).lower())
        if seq.ratio() > max_score:
            max_score = seq.ratio()
            best_match = watched_plate
    if max_score > config.fuzzy_match:
        logger.info(f"Best fuzzy_match: {best_match} ({max_score})")
        return best_match, max_score, config_watched_plates
    else:
        logger.info(f"Fuzzy match too low for : {best_match} with score ({max_score})")
        return None, None, None


def save_image(config:Config, snapshot_path,  plate_score, snapshot, camera_name, plate_number, frigate_id, logger):
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
    annotated_frame = get_alpr(config, logger).draw_predictions(frame)
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


def get_plate(config:Config, snapshot, logger):
    if config.fast_alpr:
        detected_plate_number, detected_plate_score = fast_alpr(config, snapshot, logger)
    else:
        logger.error("Plate Recognizer is not configured")
        return None, None, None, None

    return detected_plate_number, detected_plate_score

def is_plate_found_for_event(config, frigate_event_id, logger):
    columns = 'matched'
    where = 'frigate_event_id = ?'
    params = (frigate_event_id,)
    results = select_from_table(config.db_path , config.table, columns,  where, params, logger)
    return bool(results[0]['matched']) if results else False

def get_db_event_direction(config, frigate_event_id, logger):
    columns = 'vehicle_direction'
    where = 'frigate_event_id = ?'
    params = (frigate_event_id,)
    results = select_from_table(config.db_path , config.table, columns,  where, params, logger )
    return results

def delete_old_images(days_to_keep_images_in_days, path, logger):

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