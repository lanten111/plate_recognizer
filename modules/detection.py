import base64
import difflib
import json
import os
import time
from datetime import datetime

import cv2
import numpy as np
import requests
import paho.mqtt.client as mqtt

from modules.alpr import get_alpr, fast_alpr
from modules.config import Config
from modules.database import insert_into_table, select_from_table, update_table

event_type = None

def process_plate_detection(config:Config, camera_name, frigate_event_id, logger):
    timestamp = datetime.now()
    print(f"{timestamp} start processing event {frigate_event_id}")
    snapshot = get_latest_snapshot(config, frigate_event_id, camera_name, logger)

    if not is_plate_found_for_event(config, frigate_event_id, logger):
        detected_plate, detected_plate_score = get_plate(config, snapshot, logger)
        watched_plate, fuzzy_score, watched_plates = check_watched_plates(config, detected_plate, logger)

        if watched_plate is not None and fuzzy_score is not None:
            print(f"{datetime.now()} storing plate({detected_plate}) in db")
            store_plate_in_db(config, None, detected_plate, round(fuzzy_score, 2), frigate_event_id, camera_name, watched_plate, True, logger)
            print(f"{datetime.now()} saving  plate({detected_plate}) image")
            image_path = save_image(config, detected_plate_score, snapshot, camera_name, detected_plate, logger)
            print(f"{datetime.now()} sending mqtt message for  plate({detected_plate})")
            send_mqtt_message(config , detected_plate_score, frigate_event_id, camera_name, detected_plate, watched_plate, watched_plates, fuzzy_score, image_path, logger)
            config.executor.submit(delete_old_files, config, logger)
            print(f"plate({detected_plate}) match found in watched plates ({watched_plate}) for event {frigate_event_id}, {event_type} stops")
    else:
        print(f"plate already found for event {frigate_event_id}, {event_type} skipping........")

def get_latest_snapshot(config:Config, frigate_event_id, camera_name, logger):
    timestamp = datetime.now()
    start_time = time.time()
    print(f"*********{timestamp} Getting snapshot for event: {frigate_event_id}")
    snapshot_url = f"{config.frigate_url}/api/{camera_name}/latest.jpg"

    logger.debug(f"event URL: {snapshot_url}")

    parameters = {"quality": 100}
    response = requests.get(snapshot_url, params=parameters)
    snapshot = response.content
    print(f"*********{timestamp} done snapshot for event: {frigate_event_id}")
    end_time = time.time()
    duration = end_time - start_time
    print(f"*********The process took {duration:.2f} seconds to complete.")
    return snapshot


def store_plate_in_db(config:Config, detection_time, detected_plate_number, fuzzy_score, frigate_event_id, camera_name, watched_plate, plate_found, logger ):
    columns = '*'
    where = 'frigate_event_id = ?'
    params = (frigate_event_id,)
    results = select_from_table(config.db_path, config.table, columns, where, params)
    if results:
        set_clause = 'detection_time = ?, fuzzy_score = ?, detected_plate_number = ? , camera_name = ?, watched_plate = ?, plate_found = ?'
        where = 'frigate_event_id = ?'
        params = (detection_time, fuzzy_score, detected_plate_number, camera_name, watched_plate.number, plate_found,  frigate_event_id)
        update_table(config.db_path, config.table, set_clause, where, params)
    else:
        columns = ('detection_time', 'fuzzy_score', 'detected_plate_number', 'frigate_event_id', 'camera_name','watched_plate', 'plate_found')
        values = (detection_time, fuzzy_score, detected_plate_number, frigate_event_id, camera_name, watched_plate.number,plate_found )
        insert_into_table(config.db_path, config.table, columns, values)


def get_vehicle_direction(config:Config,  after_data, frigate_event_id, logger):
    direction = get_db_event_direction(config, frigate_event_id, logger)
    if len(after_data['current_zones']) > 0:
        if len(direction) == 0:
            current_zone = after_data['current_zones'][0]
            cameras = config.camera
            for camera in cameras:
                if camera == after_data['camera']:
                    zones = cameras.get(camera).zones
                    if current_zone == zones[0]:
                        values = (frigate_event_id, 'entering')
                    else:
                        values = (frigate_event_id, 'exiting')
                    columns = ('frigate_event_id', 'vehicle_direction')
                    insert_into_table(config.db_path, 'plates', columns, values)
        else:
            print(f"event  {frigate_event_id} vehicle direction exit as {direction}.")
    else:
        print(f"event  {frigate_event_id} does not contain zone, skipping direction detection")

def send_mqtt_message(config, plate_score, frigate_event_id, camera_name, detected_plate, watched_plate, watched_plates, fuzzy_score, image_path, logger):
    timestamp = datetime.now().strftime(config.date_format)

    vehicle_data = {
        'fuzzy_score': round(fuzzy_score,2),
        'matched': False,
        'detected_plate_number': str(detected_plate).upper(),
        'detected_plate_ocr_score': round(plate_score,2),
        'frigate_event_id': frigate_event_id,
        'watched_plates': json.dumps(watched_plates),
        'camera_name': camera_name,
        "plate_image": image_path,
        'watched_plate': str(watched_plate).upper(),
        'vehicle_direction': "",
        "vehicle_owner": watched_plate.owner,
        "vehicle_brand": watched_plate.car_brand

    }

    vehicle_data['matched'] = vehicle_data['fuzzy_score'] > 0.8
    vehicle_direction = select_from_table(config.db_path, "plates")
    if vehicle_direction:
        vehicle_data['vehicle_direction'] = vehicle_direction[0]
    def encode_image_to_base64(image_path):
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")
    vehicle_data['plate_image'] = encode_image_to_base64(image_path)

    device_config = {
        "name": "Plate Detection",
        "identifiers": "License Plate Detection",
        "manufacturer": "Skydyne Projects",
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
            print(f" {timestamp} sending mqtt on")
            config.executor.submit(publish_message, discovery_topic, state_topic, payload, value )
            config.executor.submit(reset_binary_sensor_state_after_delay,config, state_topic, config.watched_binary_sensor_reset_in_sec, value)


        elif key == "plate_image":
            discovery_topic = f"homeassistant/camera/vehicle_data/{key}/config"
            state_topic = f"homeassistant/camera/vehicle_data/{key}/state"

            payload = {
                "name": "plate image",
                "state_topic": state_topic,
                "unique_id": f"vehicle_camera_{key}",
                "device": device_config
            }
            config.executor.submit(publish_message, config, discovery_topic, state_topic, payload, value )
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
            config.executor.submit(publish_message, discovery_topic, state_topic,payload, value )

def publish_message(config, discovery_topic, state_topic, payload, value):
    mqtt_client = get_mqtt_client(config)
    mqtt_client.publish(discovery_topic, json.dumps(payload), retain=True)
    mqtt_client.publish(state_topic, value, retain=True)

def reset_binary_sensor_state_after_delay(config, state_topic, delay, value):
    mqtt_client = get_mqtt_client(config)
    time.sleep(delay)
    mqtt_client.publish(state_topic, not value, retain=True)
    print(f"Binary sensor state set to OFF after {delay} seconds.")

def check_watched_plates(config:Config, detected_plate, logger):
    config_watched_plates = config.watched_plates
    if not config_watched_plates:
        logger.debug("Skipping checking Watched Plates because watched_plates is not set")
        return None, None, None

    if not detected_plate:
        logger.debug("Skipping checking Watched Plates because no plate detected")
        return None, None, None

    if config.fuzzy_match == 0:
        logger.debug(f"Skipping fuzzy matching because fuzzy_match value not set in config")
        return None, None, None

    best_match, max_score = None, 0
    for watched_plate in config_watched_plates:
        seq = difflib.SequenceMatcher(a=str(detected_plate).lower(), b=str(watched_plate.number).lower())
        if seq.ratio() > max_score:
            max_score = seq.ratio()
            best_match = watched_plate

    logger.debug(f"Best fuzzy_match: {best_match} ({max_score})")

    return best_match, max_score, config_watched_plates


def save_image(config:Config, plate_score, snapshot, camera_name, plate_number, logger):

    timestamp = datetime.now().strftime(config.date_format)
    image_name = f"{camera_name}_{timestamp}.png"
    if plate_number:
        image_name = f"{str(plate_number).upper()}_{int(plate_score* 100)}%_{image_name}"
    image_path = f"{config.snapshot_path}/{image_name}"

    image_array = np.frombuffer(snapshot, np.uint8)
    frame = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    annotated_frame = get_alpr(config, logger).draw_predictions(frame)
    cv2.imwrite(image_path, annotated_frame)

    logger.info(f"Saving image with path: {image_path}")
    return image_path

#
# def save_snap(snapshot, camera_name):
#     test_image_dir = SNAPSHOT_PATH + "/test"
#     os.makedirs(test_image_dir, exist_ok=True)
#     timestamp = datetime.now().strftime(DATETIME_FORMAT)
#     image_name = f"{camera_name}_{timestamp}_{uuid.uuid4()}.png"
#     image_path = f"{test_image_dir}/{image_name}"
#     with open(image_path, "wb") as file:
#         file.write(snapshot)
#         print(f"{timestamp} saved snapshot {image_path}")


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
    results = select_from_table(config.db_path , config.table, columns,  where, params )
    return bool(results[0]['plate_found']) if results else False

def get_db_event_direction(config, frigate_event_id, logger):
    columns = 'vehicle_direction'
    where = 'frigate_event_id = ?'
    params = (frigate_event_id,)
    results = select_from_table(config.db_path , config.table, columns,  where, params )
    return results

def get_mqtt_client(config) -> mqtt:
    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    mqtt_client.enable_logger()
    mqtt_client.username_pw_set(config.mqtt_username, config.mqtt_password)
    mqtt_client.connect(config.mqtt_server, config.mqtt_port)
    return  mqtt_client

def delete_old_files(config, logger):

    folder_path = config.snapshot_path
    days=config.get('days_to_keep_images_in_days')

    now = time.time()
    cutoff = now - (days * 86400)  # 86400 seconds in a day

    for filename in os.listdir(folder_path):
        file_path = os.path.join(folder_path, filename)

        if os.path.isfile(file_path):
            file_mtime = os.path.getmtime(file_path)
            if file_mtime < cutoff:
                try:
                    os.remove(file_path)
                    print(f"Deleted: {file_path}")
                except Exception as e:
                    print(f"Failed to delete {file_path}: {e}")