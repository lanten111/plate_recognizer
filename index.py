#!/bin/python3
import base64
import threading
import concurrent.futures
import os
import sqlite3
import time
import logging
import uuid
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
import paho.mqtt.client as mqtt
import yaml
import sys
import json
import requests
import difflib
from fast_alpr import ALPR


mqtt_client = None
config = None
first_message = True
_LOGGER = None

executor = None

VERSION = '2.1.1'

executor = ThreadPoolExecutor(max_workers=5)
# set local paths for development
LOCAL = os.getenv('LOCAL', False)

CONFIG_PATH = f"{'' if LOCAL else '/'}config/config.yml"
DB_PATH = f"{'' if LOCAL else '/'}config/frigate_plate_recogizer.db"
LOG_FILE = f"{'' if LOCAL else '/'}config/frigate_plate_recogizer.log"
SNAPSHOT_PATH = f"{'' if LOCAL else '/'}plates"

DATETIME_FORMAT = "%Y-%m-%d_%H-%M-%S"

logging.getLogger("fast_alpr").setLevel(logging.FATAL)


DEFAULT_OBJECTS = ['car', 'motorcycle', 'bus']
CURRENT_EVENTS = {}

matched = None
event_type = None

def on_connect(mqtt_client, userdata, flags, reason_code, properties):
    _LOGGER.info("MQTT Connected")
    mqtt_client.subscribe(config['frigate']['main_topic'] + "/events")

def on_disconnect(mqtt_client, userdata, flags, reason_code, properties):
    if reason_code != 0:
        _LOGGER.warning(f"Unexpected disconnection, trying to reconnect userdata:{userdata}, flags:{flags}, properties:{properties}")
        while True:
            try:
                mqtt_client.reconnect()
                break
            except Exception as e:
                _LOGGER.warning(f"Reconnection failed due to {e}, retrying in 60 seconds")
                time.sleep(60)
    else:
        _LOGGER.error("Expected disconnection")

def on_message(client, userdata, message):
   process_message(message)

def process_message(message):

    global event_type
    global matched
    payload_dict = json.loads(message.payload)
    _LOGGER.debug(f"MQTT message: {payload_dict}")

    before_data = payload_dict.get("before", {})
    after_data = payload_dict.get("after", {})
    event_type = payload_dict.get("type", "")
    frigate_url = config["frigate"]["frigate_url"]
    frigate_event_id = after_data["id"]

    if check_invalid_event(before_data, after_data):
        return

    if is_duplicate_event(frigate_event_id):
        return

    if event_type == "new":
        print(f"Starting new thread for new event{event_type} for {frigate_event_id}***************")
        matched = False
        thread = threading.Thread(
            target=process_event,
            args=(before_data, after_data, frigate_url, frigate_event_id),
            daemon=True,
        )
        thread.start()

def process_event(before_data, after_data, frigate_url, frigate_event_id):
    global event_type
    loop = 0
    while event_type in ["update", "new"] and not is_plate_found_for_event(frigate_event_id):
        loop=loop + 1
        timestamp = datetime.now()
        print(f"{timestamp} start processing loop {loop} for {frigate_event_id}")
        executor.submit(process_events , after_data, frigate_url, frigate_event_id)
        time.sleep(0.5)

    print(f"Done processing event {frigate_event_id}, {event_type}")

def process_events(after_data, frigate_url, frigate_event_id):
    timestamp = datetime.now()
    print(f"{timestamp} start processing event {frigate_event_id}")
    snapshot = get_latest_snapshot(frigate_event_id, frigate_url, after_data['camera'])

    if not is_plate_found_for_event(frigate_event_id):
        detected_plate_number, detected_plate_score = get_plate(snapshot)
        watched_plate, fuzzy_score = check_watched_plates(detected_plate_number)

        if watched_plate is not None and fuzzy_score is not None:
            start_time = datetime.fromtimestamp(after_data['start_time'])
            formatted_start_time = start_time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"{datetime.now()} storing plate({detected_plate_number}) in db")
            store_plate_in_db(formatted_start_time, detected_plate_number, fuzzy_score, frigate_event_id,after_data['camera'], watched_plate, True)
            print(f"{datetime.now()} saving  plate({detected_plate_number}) image")
            image_path = save_image(config,detected_plate_score,snapshot,after_data,frigate_url,frigate_event_id,plate_number=detected_plate_number)
            print(f"{datetime.now()} sending mqtt message for  plate({detected_plate_number})")
            send_mqtt_message(detected_plate_number, detected_plate_score, frigate_event_id, after_data, watched_plate,config['frigate'].get('watched_plates'),  fuzzy_score,image_path)
            executor.submit(delete_old_files)
            print(f"plate({detected_plate_number}) match found in watched plates ({watched_plate}) for event {frigate_event_id}, {event_type} stops")
    else:
        print(f"plate already found for event {frigate_event_id}, {event_type} skipping........")


def get_alpr():
    plate_detector_model = config['fast_alpr'].get('plate_detector_model')
    ocr_model = config['fast_alpr'].get('ocr_model')
    return ALPR(
        detector_model=plate_detector_model,
        ocr_model=ocr_model,ocr_device="cpu",ocr_model_path=CONFIG_PATH + "/models"
    )
def fast_alpr(snapshot):
    plate_detector_model = config['fast_alpr'].get('plate_detector_model')
    ocr_model = config['fast_alpr'].get('ocr_model')
    alpr = ALPR(
        detector_model=plate_detector_model,
        ocr_model=ocr_model,ocr_device="cpu",ocr_model_path=CONFIG_PATH + "/models"
    )

    image_array = np.frombuffer(snapshot, np.uint8)
    frame = cv2.imdecode(image_array, cv2.IMREAD_COLOR)

    alpr_results = get_alpr().predict(frame)

    print(alpr_results)

    for result in alpr_results:
        ocr_text = result.ocr.text
        ocr_confidence = result.ocr.confidence

    return ocr_text, ocr_confidence

def check_watched_plates(plate_number):
    config_watched_plates = config['frigate'].get('watched_plates', [])
    if not config_watched_plates:
        _LOGGER.debug("Skipping checking Watched Plates because watched_plates is not set")
        return None, None, None
    
    config_watched_plates = [str(x).lower() for x in config_watched_plates] #make sure watched_plates are all lower case
    
    #Step 1 - test if top plate is a watched plate
    matching_plate = str(plate_number).lower() in config_watched_plates 
    if matching_plate:
        _LOGGER.info(f"Recognised plate is a Watched Plate: {plate_number}")
        return None, None, None  

    fuzzy_match = config['frigate'].get('fuzzy_match', 0) 
    
    if fuzzy_match == 0:
        _LOGGER.debug(f"Skipping fuzzy matching because fuzzy_match value not set in config")
        return None, None, None
    
    max_score = 0
    best_match = None
    for watched_plate in config_watched_plates:
        seq = difflib.SequenceMatcher(a=str(plate_number).lower(), b=str(watched_plate).lower())
        if seq.ratio() > max_score: 
            max_score = seq.ratio()
            best_match = watched_plate
    
    _LOGGER.debug(f"Best fuzzy_match: {best_match} ({max_score})")

    if max_score >= fuzzy_match:
        _LOGGER.info(f"Watched plate found from fuzzy matching: {best_match} with score {max_score}")    
        return best_match, max_score

    return None, None
    
def send_mqtt_message(plate_number, plate_score, frigate_event_id, after_data, watched_plate, watched_plates, fuzzy_score, image_path):
    timestamp = datetime.now().strftime(DATETIME_FORMAT)
    vehicle_data = {
        'fuzzy_score': round(fuzzy_score,2),
        'matched': False,
        'detected_plate_number': str(plate_number).upper(),
        'detected_plate_ocr_score': round(plate_score,2),
        'frigate_event_id': frigate_event_id,
        'watched_plates': json.dumps(watched_plates),
        'camera_name': after_data['camera'],
        "plate_image": image_path,
        'watched_plate': str(watched_plate).upper()

    }

    vehicle_data['matched'] = vehicle_data['fuzzy_score'] > 0.8

    def encode_image_to_base64(image_path):
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")
    vehicle_data['plate_image'] = encode_image_to_base64(image_path)

    device_config = {
        "name": "Plate Detection",
        "identifiers": "License Plate Detection",
        "manufacturer": "skydyne",
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
            executor.submit(publish_message, discovery_topic, state_topic, payload, value )
            executor.submit(reset_binary_sensor_state_after_delay,state_topic, 20, value)


        elif key == "plate_image":
            discovery_topic = f"homeassistant/camera/vehicle_data/{key}/config"
            state_topic = f"homeassistant/camera/vehicle_data/{key}/state"

            payload = {
                "name": "plate image",
                "state_topic": state_topic,
                "unique_id": f"vehicle_camera_{key}",
                "device": device_config
            }
            executor.submit(publish_message, discovery_topic, state_topic, payload, value )
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
            executor.submit(publish_message, discovery_topic, state_topic,payload, value )

def publish_message(discovery_topic, state_topic, payload, value):
    mqtt_client.publish(discovery_topic, json.dumps(payload), retain=True)
    mqtt_client.publish(state_topic, value, retain=True)

def reset_binary_sensor_state_after_delay(state_topic, delay, value):
    time.sleep(delay)
    mqtt_client.publish(state_topic, not value, retain=True)
    print(f"Binary sensor state set to OFF after {delay} seconds.")



def save_image(config,plate_score,snapshot, after_data, frigate_url, frigate_event_id, plate_number):
    os.makedirs(SNAPSHOT_PATH, exist_ok=True)
    timestamp = datetime.now().strftime(DATETIME_FORMAT)
    image_name = f"{after_data['camera']}_{timestamp}.png"
    if plate_number:
        image_name = f"{str(plate_number).upper()}_{int(plate_score* 100)}%_{image_name}"
    image_path = f"{SNAPSHOT_PATH}/{image_name}"

    image_array = np.frombuffer(snapshot, np.uint8)
    frame = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    # frame = cv2.imread(snapshot)
    annotated_frame = get_alpr().draw_predictions(frame)
    cv2.imwrite(image_path, annotated_frame)

    # with open(image_path, "wb") as file:
    #     file.write(snapshot)
    _LOGGER.info(f"Saving image with path: {image_path}")
    return image_path


def check_invalid_event(before_data, after_data):
    # check if it is from the correct camera or zone
    config_zones = config['frigate'].get('zones', [])
    config_cameras = config['frigate'].get('camera', [])

    matching_zone = any(value in after_data['current_zones'] for value in config_zones) if config_zones else True
    matching_camera = after_data['camera'] in config_cameras if config_cameras else True

    if not (matching_zone and matching_camera):
        _LOGGER.debug(f"Skipping event: {after_data['id']} because it does not match the configured zones/cameras")
        return True

    valid_objects = config['frigate'].get('objects', DEFAULT_OBJECTS)
    if(after_data['label'] not in valid_objects):
        _LOGGER.debug(f"is not a correct label: {after_data['label']}")
        return True

    return False


def get_latest_snapshot(frigate_event_id, frigate_url, camera_name):
    timestamp = datetime.now()
    start_time = time.time()
    print(f"*********{timestamp} Getting snapshot for event: {frigate_event_id}")
    snapshot_url = f"{frigate_url}/api/{camera_name}/latest.jpg"

    _LOGGER.debug(f"event URL: {snapshot_url}")

    parameters = {"quality": 100}
    response = requests.get(snapshot_url, params=parameters)
    snapshot = response.content
    print(f"*********{timestamp} done snapshot for event: {frigate_event_id}")
    end_time = time.time()
    duration = end_time - start_time
    print(f"*********The process took {duration:.2f} seconds to complete.")
    return snapshot

def save_snap(snapshot, camera_name):
    test_image_dir = SNAPSHOT_PATH + "/test"
    os.makedirs(test_image_dir, exist_ok=True)
    timestamp = datetime.now().strftime(DATETIME_FORMAT)
    image_name = f"{camera_name}_{timestamp}_{uuid.uuid4()}.png"
    image_path = f"{test_image_dir}/{image_name}"
    with open(image_path, "wb") as file:
        file.write(snapshot)
        print(f"{timestamp} saved snapshot {image_path}")

def get_snapshot(frigate_event_id, frigate_url, cropped, camera_name):
    _LOGGER.debug(f"Getting snapshot for event: {frigate_event_id}, Crop: {cropped}")
    snapshot_url = f"{frigate_url}/api/events/{frigate_event_id}/snapshot-clean.png"
    _LOGGER.debug(f"event URL: {snapshot_url}")

    # get snapshot
    parameters = {"crop": 1 if cropped else 0, "quality": 100}
    response = requests.get(snapshot_url, params=parameters)
    snapshot = response.content
    thread = threading.Thread(
        target=save_snap,
        args=(snapshot, camera_name),
        daemon=True,
    )
    thread.start()
    # Check if the request was successful (HTTP status code 200)
    if response.status_code != 200:
        _LOGGER.error(f"Error getting snapshot: {response.status_code}")
        return

    return snapshot


def is_duplicate_event(frigate_event_id):
     # see if we have already processed this event
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""SELECT * FROM plates WHERE frigate_event_id = ?""", (frigate_event_id,))
    row = cursor.fetchone()
    conn.close()

    if row is not None:
        _LOGGER.debug(f"Skipping event: {frigate_event_id} because it has already been processed")
        return True

    return False

def get_plate(snapshot):
    # try to get plate number
    detected_plate_number = None
    detected_plate_score = None

    if config.get('fast_alpr'):
        detected_plate_number, detected_plate_score = fast_alpr(snapshot)
    else:
        _LOGGER.error("Plate Recognizer is not configured")
        return None, None, None, None

    return detected_plate_number, detected_plate_score


def is_plate_found_for_event(frigate_event_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""SELECT plate_found FROM plates WHERE frigate_event_id = ?""", (frigate_event_id,))
    result = cursor.fetchone()
    conn.close()
    return bool(result[0]) if result else None

def store_plate_in_db(detection_time, plate_number, fuzzy_score, frigate_event_id, camera_name, watched_plate, plate_found ):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    _LOGGER.info(f"Storing plate number in database: {plate_number} with score: {fuzzy_score}")

    cursor.execute("""INSERT INTO plates (detection_time, fuzzy_score , plate_number, frigate_event_id , camera_name, watched_plate, plate_found  ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                   (detection_time, fuzzy_score, plate_number, frigate_event_id, camera_name,watched_plate, plate_found)
                   )

    conn.commit()
    conn.close()

def setup_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS plates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detection_time TIMESTAMP NOT NULL,
            fuzzy_score TEXT NOT NULL,
            plate_number TEXT NOT NULL,
            frigate_event_id TEXT NOT NULL UNIQUE,
            camera_name TEXT NOT NULL,
            watched_plate TEXT NOT NULL,
            plate_found BOOLEAN NOT NULL,            
            created_date DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def load_config():
    global config
    with open(CONFIG_PATH, 'r') as config_file:
        config = yaml.safe_load(config_file)

    if SNAPSHOT_PATH and not os.path.isdir(SNAPSHOT_PATH):
        os.makedirs(SNAPSHOT_PATH)

def run_mqtt_client():
    global mqtt_client
    _LOGGER.info(f"Starting MQTT client. Connecting to: {config['frigate']['mqtt_server']}")

    # setup mqtt client
    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    mqtt_client.enable_logger()
    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client.on_message = on_message


    if config['frigate'].get('mqtt_username', False):
        username = config['frigate']['mqtt_username']
        password = config['frigate'].get('mqtt_password', '')
        mqtt_client.username_pw_set(username, password)

    mqtt_client.connect(config['frigate']['mqtt_server'], config['frigate'].get('mqtt_port', 1883))
    mqtt_client.loop_forever()

def delete_old_files():

    folder_path = SNAPSHOT_PATH
    days=config.get('days_to_keep_images')

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

def load_logger():
    global _LOGGER
    _LOGGER = logging.getLogger(__name__)
    _LOGGER.setLevel(config.get('logger_level', 'INFO'))
    # Create a formatter to customize the log message format
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Create a console handler and set the level to display all messages
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formatter)

    # Create a file handler to log messages to a file
    file_handler = logging.FileHandler(LOG_FILE)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    # Add the handlers to the logger
    _LOGGER.addHandler(console_handler)
    _LOGGER.addHandler(file_handler)

def main():
    global executor

    load_config()
    setup_db()
    load_logger()

    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    _LOGGER.info(f"Time: {current_time}")
    _LOGGER.info(f"Python Version: {sys.version}")
    _LOGGER.info(f"Frigate Plate Recognizer Version: {VERSION}")
    _LOGGER.debug(f"config: {config}")

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)
    run_mqtt_client()


if __name__ == '__main__':
    main()
