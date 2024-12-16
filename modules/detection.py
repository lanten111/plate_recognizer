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
from modules.database import insert_into_table, select_from_table, update_table

event_type = None

def process_plate_detection(config:Config, camera_name, frigate_event_id, mqtt_client, logger):
    logger.info(f"start processing event {frigate_event_id}")
    snapshot = get_latest_snapshot(config, frigate_event_id, camera_name, logger)
    # config.executor(save_image, config, config.debug_snapshot_path,  None, snapshot, camera_name, None, frigate_event_id, logger)

    if not is_plate_found_for_event(config, frigate_event_id, logger):
        detected_plate, detected_plate_score = get_plate(config, snapshot, logger)
        watched_plate, fuzzy_score, watched_plates = check_watched_plates(config, detected_plate, logger)

        if watched_plate is not None and fuzzy_score is not None:
            store_plate_in_db(config, None, detected_plate, round(fuzzy_score, 2), frigate_event_id, camera_name, watched_plate, True, logger)
            image_path = save_image(config, config.snapshot_path,  detected_plate_score, snapshot, camera_name, detected_plate, frigate_event_id, logger)
            send_mqtt_message(config , detected_plate_score, frigate_event_id, camera_name, detected_plate,
                              watched_plate, watched_plates, fuzzy_score, image_path, mqtt_client, logger)
            config.executor.submit(delete_old_files, config.days_to_keep_images_in_days, config.debug_snapshot_path, logger)
            config.executor.submit(delete_old_files, config.days_to_keep_images_in_days, config.snapshot_path, logger)
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


def store_plate_in_db(config:Config, detection_time, detected_plate_number, fuzzy_score, frigate_event_id, camera_name, watched_plate, plate_found, logger ):
    logger.info(f"storing plate({detected_plate_number}) in db for event {frigate_event_id}")
    columns = '*'
    where = 'frigate_event_id = ?'
    params = (frigate_event_id,)
    results = select_from_table(config.db_path, config.table, columns, where, params, logger)
    if results:
        set_clause = 'detection_time = ?, fuzzy_score = ?, detected_plate_number = ? , camera_name = ?, watched_plate = ?, plate_found = ?'
        where = 'frigate_event_id = ?'
        params = (detection_time, fuzzy_score, detected_plate_number, camera_name, watched_plate.number, plate_found,  frigate_event_id)
        update_table(config.db_path, config.table, set_clause, where, params, logger)
        logger.info(f"updated db for event  {frigate_event_id}.")
    else:
        columns = ('detection_time', 'fuzzy_score', 'detected_plate_number', 'frigate_event_id', 'camera_name','watched_plate', 'plate_found')
        values = (detection_time, fuzzy_score, detected_plate_number, frigate_event_id, camera_name, watched_plate.number,plate_found )
        insert_into_table(config.db_path, config.table, columns, values, logger)
        logger.info(f"inserted db for event  {frigate_event_id}.")


def get_vehicle_direction(config:Config,  after_data, frigate_event_id, logger):
    direction = get_db_event_direction(config, frigate_event_id, logger)
    if len(after_data['current_zones']) > 0:
        if len(direction) == 0:
            current_zone = after_data['current_zones'][0]
            cameras = config.camera
            for camera in cameras:
                if camera == after_data['camera']:
                    if current_zone.lower() == config.camera.get(camera).first_zone:
                        values = (frigate_event_id, 'entering')
                    elif current_zone.lower() == config.camera.get(camera).last_zone:
                        values = (frigate_event_id, 'exiting')
                    else:
                        values = (frigate_event_id, 'unknown')
                    columns = ('frigate_event_id', 'vehicle_direction')
                    insert_into_table(config.db_path, config.table, columns, values, logger)
        else:
            logger.info(f"event  {frigate_event_id} vehicle direction exit as {direction}.")
    else:
        logger.info(f"event  {frigate_event_id} does not contain zone, skipping direction detection")

def send_mqtt_message(config, plate_score, frigate_event_id, camera_name, detected_plate, watched_plate, watched_plates, fuzzy_score, image_path, mqtt_client, logger):
    logger.info(f"{datetime.now()} sending mqtt message for  plate({detected_plate})")
    timestamp = datetime.now().strftime(config.date_format)

    def watched_plates_to_json(watched_plates) -> str:
        plates_as_dicts = [asdict(plate) for plate in watched_plates]
        return json.dumps(plates_as_dicts, indent=4)

    vehicle_data = {
        'fuzzy_score': round(fuzzy_score,2),
        'matched': False,
        'detected_plate_number': str(detected_plate).upper(),
        'detected_plate_ocr_score': round(plate_score,2),
        'frigate_event_id': frigate_event_id,
        'watched_plates': watched_plates_to_json(watched_plates),
        'camera_name': camera_name,
        "plate_image": image_path,
        'watched_plate': str(watched_plate).upper(),
        'vehicle_direction': "",
        "vehicle_owner": watched_plate.owner,
        "vehicle_brand": watched_plate.car_brand

    }

    vehicle_data['matched'] = vehicle_data['fuzzy_score'] > 0.8
    columns = 'vehicle_direction'
    where = 'frigate_event_id = ?'
    params = (frigate_event_id,)
    vehicle_direction = select_from_table(config.db_path, config.table, columns, where, params, logger)
    if vehicle_direction:
        vehicle_data['vehicle_direction'] = vehicle_direction[0]['vehicle_direction']
    def encode_image_to_base64(image_path):
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")
    vehicle_data['plate_image'] = encode_image_to_base64(image_path)

    device_config = {
        "name": "Plate Detection",
        "identifiers": "License Plate Detection",
        "manufacturer": config.manufacturer,
        "sw_version": "1.0"
    }

    for key, value in vehicle_data.items():
        if key == "matched":
            # Binary Sensor Configuration
            discovery_topic = f"homeassistant/binary_sensor/vehicle_data/{key}/config"
            state_topic = f"homeassistant/binary_sensor/vehicle_data/{key}/state"

            payload = {
                "name": "matched",
                "state_topic": state_topic,
                "payload_on": "True",
                "payload_off": "False",
                "device_class": "motion",
                "unique_id": f"vehicle_binary_sensor_{key}",
                "device": device_config
            }
            logger.info(f"sending mqtt on")
            config.executor.submit(publish_message,config, discovery_topic, state_topic, payload, value, mqtt_client, logger)
            config.executor.submit(reset_binary_sensor_state_after_delay,config, state_topic, config.watched_binary_sensor_reset_in_sec, value, mqtt_client, logger)


        elif key == "plate_image":
            discovery_topic = f"homeassistant/camera/vehicle_data/{key}/config"
            state_topic = f"homeassistant/camera/vehicle_data/{key}/state"

            payload = {
                "name": "plate image",
                "state_topic": state_topic,
                "unique_id": f"vehicle_camera_{key}",
                "device": device_config
            }
            config.executor.submit(publish_message, config, discovery_topic, state_topic, payload, value,mqtt_client,  logger )
        else:
            discovery_topic = f"homeassistant/sensor/vehicle_data/{key}/config"
            state_topic = f"homeassistant/sensor/vehicle_data/{key}/state"

            payload = {
                "name": f"{key.replace('_', ' ').title()}",
                "state_topic": state_topic,
                "unit_of_measurement": None,
                "value_template": "{{ value }}",
                "unique_id": f"vehicle_sensor_{key}",
                "device": device_config
            }

            # Adjust unit_of_measurement for specific fields
            if key == "ocr_score":
                payload["unit_of_measurement"] = "%"
            config.executor.submit(publish_message,config, discovery_topic, state_topic,payload, value, mqtt_client, logger )

def publish_message(config, discovery_topic, state_topic, payload, value, mqtt_client, logger):
    mqtt_client.publish(discovery_topic, json.dumps(payload), retain=True)
    mqtt_client.publish(state_topic, value, retain=True)
    logger.info(f"successful sent detected plate to mqtt {discovery_topic}")

def reset_binary_sensor_state_after_delay(config, state_topic, delay, value, mqtt_client, logger):
    time.sleep(delay)
    mqtt_client.publish(state_topic, not value, retain=True)
    logger.info(f"Binary sensor state set to OFF after {delay} seconds.")

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

    logger.info(f"Best fuzzy_match: {best_match} ({max_score})")

    return best_match, max_score, config_watched_plates


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
    columns = 'plate_found'
    where = 'frigate_event_id = ?'
    params = (frigate_event_id,)
    results = select_from_table(config.db_path , config.table, columns,  where, params, logger)
    return bool(results[0]['plate_found']) if results else False

def get_db_event_direction(config, frigate_event_id, logger):
    columns = 'vehicle_direction'
    where = 'frigate_event_id = ?'
    params = (frigate_event_id,)
    results = select_from_table(config.db_path , config.table, columns,  where, params, logger )
    return results

def delete_old_files(days_to_keep_images_in_days, path, logger):

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