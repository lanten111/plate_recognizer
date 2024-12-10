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

DATETIME_FORMAT = "%Y-%m-%d_%H-%M"



DEFAULT_OBJECTS = ['car', 'motorcycle', 'bus']
CURRENT_EVENTS = {}


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

def set_sublabel(frigate_url, frigate_event_id, sublabel, score):
    post_url = f"{frigate_url}/api/events/{frigate_event_id}/sub_label"
    _LOGGER.debug(f'sublabel: {sublabel}')
    _LOGGER.debug(f'sublabel url: {post_url}')

    # frigate limits sublabels to 20 characters currently
    if len(sublabel) > 20:
        sublabel = sublabel[:20]

    sublabel = str(sublabel).upper() # plates are always upper cased

    # Submit the POST request with the JSON payload
    payload = { "subLabel": sublabel }
    headers = { "Content-Type": "application/json" }
    response = requests.post(post_url, data=json.dumps(payload), headers=headers)

    percent_score = "{:.1%}".format(score)

    # Check for a successful response
    if response.status_code == 200:
        _LOGGER.info(f"Sublabel set successfully to: {sublabel} with {percent_score} confidence")
    else:
        _LOGGER.error(f"Failed to set sublabel. Status code: {response.status_code}")

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

def check_watched_plates(plate_number, response):
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
    
    #Step 2 - test against AI candidates:
    for i, plate in enumerate(response): 
        matching_plate = plate.get('plate') in config_watched_plates
        if matching_plate:
            if config.get('plate_recognizer'):
                score = plate.get('score')
            else: 
                if i == 0: continue  #skip first response for CodeProjet.AI as index 0 = original plate.
                score = plate.get('confidence')
            _LOGGER.info(f"Watched plate found from AI candidates: {plate.get('plate')} with score {score}")
            return plate.get('plate'), score, None
    
    _LOGGER.debug("No Watched Plates found from AI candidates")
    
    #Step 3 - test against fuzzy match:
    fuzzy_match = config['frigate'].get('fuzzy_match', 0) 
    
    if fuzzy_match == 0:
        _LOGGER.debug(f"Skipping fuzzy matching because fuzzy_match value not set in config")
        return None, None, None
    
    max_score = 0
    best_match = None
    for candidate in config_watched_plates:
        seq = difflib.SequenceMatcher(a=str(plate_number).lower(), b=str(candidate).lower())
        if seq.ratio() > max_score: 
            max_score = seq.ratio()
            best_match = candidate
    
    _LOGGER.debug(f"Best fuzzy_match: {best_match} ({max_score})")

    if max_score >= fuzzy_match:
        _LOGGER.info(f"Watched plate found from fuzzy matching: {best_match} with score {max_score}")    
        return best_match, None, max_score
        

    _LOGGER.debug("No matching Watched Plates found.")
    #No watched_plate matches found 
    return None, None, None
    
def send_mqtt_message(plate_number, plate_score, frigate_event_id, after_data, formatted_start_time, watched_plate, fuzzy_score, image_path):
    MQTT_TOPIC = "homeassistant/sensor/vehicle_data"
    vehicle_data = {
        'detected_plate_number': str(plate_number).upper(),
        'detected_plate_ocr_score': round(plate_score,2),
        'frigate_event_id': frigate_event_id,
        'camera_name': after_data['camera'],
        'time': "",
        "plate_image": ""
    }

    if watched_plate:
        vehicle_data.update({
            'fuzzy_score': round(fuzzy_score,2),
            'watched_plate': str(watched_plate).upper(),
            'matched': False
        })

    vehicle_data['matched'] = vehicle_data['fuzzy_score'] > 0.8
    vehicle_data['time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def encode_image_to_base64(image_path):
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")
    vehicle_data['plate_image'] = encode_image_to_base64(image_path)
    device_config = {
        "name": "Plate Detection",
        "identifiers": "License Plate Detection",
        "manufacturer": "skydyne",
        "sw_version": "1.0",
    }

    for key, value in vehicle_data.items():
        if key == "matched":
            # Binary Sensor Configuration
            discovery_topic = f"homeassistant/binary_sensor/vehicle_data/{key}/config"
            state_topic = f"{MQTT_TOPIC}/{key}/state"

            payload = {
                "name": "watched plate match",
                "state_topic": state_topic,
                "payload_on": "ON",
                "payload_off": "OFF",
                "device_class": "motion",
                "unique_id": f"vehicle_binary_sensor_{key}",
                "device": device_config,
            }
            mqtt_client.publish(discovery_topic, json.dumps(payload), retain=True)
            mqtt_client.publish(state_topic, "ON" if value else "OFF", retain=True)
        elif key == "time":
            discovery_topic = f"homeassistant/sensor/vehicle_data/{key}/config"
            state_topic = f"{MQTT_TOPIC}/{key}/state"

            payload = {
                "name": "time",
                "state_topic": state_topic,
                "device_class": "timestamp",
                "unique_id": f"vehicle_sensor_{key}",
                "device": device_config,
            }
            mqtt_client.publish(discovery_topic, json.dumps(payload), retain=True)
            mqtt_client.publish(state_topic, value, retain=True)
        elif key == "plate_image":
            # Camera Configuration
            discovery_topic = f"homeassistant/camera/vehicle_data/{key}/config"
            state_topic = f"{MQTT_TOPIC}/{key}/state"

            payload = {
                "name": "image",
                "state_topic": state_topic,
                "image": value,  # Use the URL for the camera image
                "unique_id": f"vehicle_camera_{key}",
                "device": device_config,  # Attach to the same device
            }
            mqtt_client.publish(discovery_topic, json.dumps(payload), retain=True)
            mqtt_client.publish(state_topic, value, retain=True)
        else:
            # Regular Sensor Configuration
            discovery_topic = f"{MQTT_TOPIC}/{key}/config"
            state_topic = f"{MQTT_TOPIC}/{key}/state"

            payload = {
                "name": f"{key.replace('_', ' ').title()}",
                "state_topic": state_topic,
                "unit_of_measurement": None,
                "value_template": "{{ value }}",
                "unique_id": f"vehicle_sensor_{key}",
                "device": device_config,  # Attach to the same device
            }

            # Adjust unit_of_measurement for specific fields
            if key == "ocr_score":
                payload["unit_of_measurement"] = "%"
            mqtt_client.publish(discovery_topic, json.dumps(payload), retain=True)
            mqtt_client.publish(state_topic, value, retain=True)


def has_common_value(array1, array2):
    return any(value in array2 for value in array1)

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

# def check_first_message():
#     global first_message
#     if first_message:
#         first_message = False
#         _LOGGER.debug("Skipping first message")
#         return True
#     return False

def check_invalid_event(before_data, after_data):
    # check if it is from the correct camera or zone
    config_zones = config['frigate'].get('zones', [])
    config_cameras = config['frigate'].get('camera', [])

    matching_zone = any(value in after_data['current_zones'] for value in config_zones) if config_zones else True
    matching_camera = after_data['camera'] in config_cameras if config_cameras else True

    # Check if either both match (when both are defined) or at least one matches (when only one is defined)
    if not (matching_zone and matching_camera):
        _LOGGER.debug(f"Skipping event: {after_data['id']} because it does not match the configured zones/cameras")
        return True

    # check if it is a valid object
    valid_objects = config['frigate'].get('objects', DEFAULT_OBJECTS)
    if(after_data['label'] not in valid_objects):
        _LOGGER.debug(f"is not a correct label: {after_data['label']}")
        return True

    # # limit api calls to plate checker api by only checking the best score for an event
    # if(before_data['top_score'] == after_data['top_score'] and after_data['id'] in CURRENT_EVENTS) and not config['frigate'].get('frigate_plus', False):
    #     _LOGGER.debug(f"duplicated snapshot from Frigate as top_score from before and after are the same: {after_data['top_score']} {after_data['id']}")
    #     return True
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
    # thread = threading.Thread(
    #     target=save_snap,
    #     args=(snapshot, camera_name),
    #     daemon=True,
    # )
    # thread.start()
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

def get_license_plate_attribute(after_data):
    if config['frigate'].get('frigate_plus', False):
        attributes = after_data.get('current_attributes', [])
        license_plate_attribute = [attribute for attribute in attributes if attribute['label'] == 'license_plate']
        return license_plate_attribute
    else:
        return None
    
def get_final_data(event_url):
    if config['frigate'].get('frigate_plus', False):
        response = requests.get(event_url)
        if response.status_code != 200:
            _LOGGER.error(f"Error getting final data: {response.status_code}")
            return
        event_json = response.json()
        event_data = event_json.get('data', {})
    
        if event_data:
            attributes = event_data.get('attributes', [])
            final_attribute = [attribute for attribute in attributes if attribute['label'] == 'license_plate']
            return final_attribute
        else:
            return None
    else:
        return None
    

# def is_valid_license_plate(after_data):
#     # if user has frigate plus then check license plate attribute
#     after_license_plate_attribute = get_license_plate_attribute(after_data)
#     if not any(after_license_plate_attribute):
#         _LOGGER.debug(f"no license_plate attribute found in event attributes")
#         return False
#
#     # check min score of license plate attribute
#     license_plate_min_score = config['frigate'].get('license_plate_min_score', 0)
#     if after_license_plate_attribute[0]['score'] < license_plate_min_score:
#         _LOGGER.debug(f"license_plate attribute score is below minimum: {after_license_plate_attribute[0]['score']}")
#         return False
#
#     return True

def is_duplicate_event(frigate_event_id):
     # see if we have already processed this event
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""SELECT * FROM plates WHERE frigate_event = ?""", (frigate_event_id,))
    row = cursor.fetchone()
    conn.close()

    if row is not None:
        _LOGGER.debug(f"Skipping event: {frigate_event_id} because it has already been processed")
        return True

    return False

def get_plate(snapshot):
    # try to get plate number
    plate_number = None
    plate_score = None

    if config.get('fast_alpr'):
        plate_number, plate_score = fast_alpr(snapshot)
    else:
        _LOGGER.error("Plate Recognizer is not configured")
        return None, None, None, None

    watched_plates = config['frigate'].get('watched_plates')
    if watched_plates:
        for watched_plate in watched_plates:
            fuzzy_score = difflib.SequenceMatcher(None, watched_plate.lower(), plate_number.lower()).ratio()

            min_score = config['frigate'].get('min_score')
            if fuzzy_score < min_score:
                _LOGGER.info(f"Score is below minimum: {fuzzy_score} for a match between {watched_plate} and ({plate_number})")

            return plate_number, plate_score, watched_plate, fuzzy_score
    else:
        return plate_number, plate_score, None, None


def store_plate_in_db(plate_number, plate_score, frigate_event_id, after_data, formatted_start_time):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    _LOGGER.info(f"Storing plate number in database: {plate_number} with score: {plate_score}")

    cursor.execute("""INSERT INTO plates (detection_time, score, plate_number, frigate_event, camera_name) VALUES (?, ?, ?, ?, ?)""",
        (formatted_start_time, plate_score, plate_number, frigate_event_id, after_data['camera'])
    )

    conn.commit()
    conn.close()

def on_message(client, userdata, message):
    global executor
    executor.submit(process_message, message)

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
        print(f"Starting new thread for {event_type} for {frigate_event_id}")
        matched = False
        thread = threading.Thread(
            target=get_snapshots,
            args=(before_data, after_data, frigate_url, frigate_event_id),
            daemon=True,
        )
        thread.start()

def get_snapshots(before_data, after_data, frigate_url, frigate_event_id):
    global event_type
    global matched
    while event_type in ["update", "new"] and not matched:
        timestamp = datetime.now()
        print(f"{timestamp} Getting snapshot {event_type} for {frigate_event_id}")
        start_time = time.time()
        snapshot = get_latest_snapshot(frigate_event_id, frigate_url, after_data['camera'])
        # snapshot = get_snapshot(frigate_event_id, frigate_url, 0, after_data['camera'])
        # thread = threading.Thread(
        #     target=process_snapshot,
        #     args=(before_data, after_data, frigate_url, frigate_event_id, snapshot),
        #     daemon=True,
        # )
        # thread.start()
        executor.submit(process_snapshot, before_data, after_data, frigate_url, frigate_event_id, snapshot)
        end_time = time.time()
        duration = end_time - start_time
        print(f"The process took {duration:.2f} seconds to complete.")
        timestamp = datetime.now()
        print(f"{timestamp} Done getting snapshot {event_type} for {frigate_event_id}")

    print(f"Done processing event {frigate_event_id}, {event_type}")

def process_snapshot(before_data, after_data, frigate_url, frigate_event_id, snapshot):
    global matched
    
    # if type == 'end' and after_data['id'] in CURRENT_EVENTS:
    #     _LOGGER.debug(f"CLEARING EVENT: {frigate_event_id} after {CURRENT_EVENTS[frigate_event_id]} calls to AI engine")
    #     if frigate_event_id in CURRENT_EVENTS:
    #         del CURRENT_EVENTS[frigate_event_id]
    


    # frigate_plus = config['frigate'].get('frigate_plus', False)
    # if frigate_plus and not is_valid_license_plate(after_data):
    #     return
    
    # if not type == 'end' and not after_data['id'] in CURRENT_EVENTS:
    #     CURRENT_EVENTS[frigate_event_id] =  0

    # get snapshot if it exists
    # snapshot = None
    # if after_data['has_snapshot']:
    #     snapshot = get_snapshot(frigate_event_id, frigate_url, True)
    # if not snapshot:
    #     _LOGGER.debug(f"Event {frigate_event_id} has no snapshot")
    #     if frigate_event_id in CURRENT_EVENTS:
    #         del CURRENT_EVENTS[frigate_event_id] # remove existing id from current events due to snapshot failure - will try again next frame
    #     return

    # _LOGGER.debug(f"Getting plate for event: {frigate_event_id}")
    # if frigate_event_id in CURRENT_EVENTS:
    #     if config['frigate'].get('max_attempts', 0) > 0 and CURRENT_EVENTS[frigate_event_id] > config['frigate'].get('max_attempts', 0):
    #         _LOGGER.debug(f"Maximum number of AI attempts reached for event {frigate_event_id}: {CURRENT_EVENTS[frigate_event_id]}")
    #         return
    #     CURRENT_EVENTS[frigate_event_id] += 1

    if not matched:
        plate_number, plate_score, watched_plate, fuzzy_score = get_plate(snapshot)
        matched = fuzzy_score > 0.9
    else:
        return
    if plate_number:
        start_time = datetime.fromtimestamp(after_data['start_time'])
        formatted_start_time = start_time.strftime("%Y-%m-%d %H:%M:%S")
        
        if watched_plate:
            store_plate_in_db(watched_plate, plate_score, frigate_event_id, after_data, formatted_start_time)
        else:
            store_plate_in_db(plate_number, plate_score, frigate_event_id, after_data, formatted_start_time)
        # set_sublabel(frigate_url, frigate_event_id, watched_plate if watched_plate else plate_number, plate_score)

        if  config['frigate'].get('save_snapshots', False):
            image_path = save_image(config,plate_score,snapshot,after_data,frigate_url,frigate_event_id,plate_number=plate_number)

        send_mqtt_message(plate_number, plate_score, frigate_event_id, after_data, formatted_start_time, watched_plate, fuzzy_score,image_path)
         




def setup_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS plates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detection_time TIMESTAMP NOT NULL,
            score TEXT NOT NULL,
            plate_number TEXT NOT NULL,
            frigate_event TEXT NOT NULL,
            camera_name TEXT NOT NULL,
            created_date DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def load_config():
    global config
    with open(CONFIG_PATH, 'r') as config_file:
        config = yaml.safe_load(config_file)

    if SNAPSHOT_PATH:
        if not os.path.isdir(SNAPSHOT_PATH):
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


    # sensor_config = {
    #     "name": "License Plate Sensor",
    #     "state_topic": STATE_TOPIC,
    #     "unit_of_measurement": "",  # No specific unit for this example
    #     "value_template": "{{ value_json.plate_number }}",
    #     "json_attributes_topic": "homeassistant/vehicle/license_plate/attributes",  # Send additional data as attributes
    #     "device": {
    #         "identifiers": UNIQUE_ID,
    #         "name": "License Plate Detector",
    #         "model": "Custom Sensor v1",
    #         "manufacturer": "My Sensors Inc.",
    #     },
    #     "unique_id": UNIQUE_ID,
    # }

    if config['frigate'].get('mqtt_username', False):
        username = config['frigate']['mqtt_username']
        password = config['frigate'].get('mqtt_password', '')
        mqtt_client.username_pw_set(username, password)

    mqtt_client.connect(config['frigate']['mqtt_server'], config['frigate'].get('mqtt_port', 1883))
    # mqtt_client.publish(DISCOVERY_TOPIC, json.dumps(sensor_config), qos=1, retain=True)
    mqtt_client.loop_forever()

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

    if config.get('plate_recognizer'):
        _LOGGER.info(f"Using Plate Recognizer API")
    if config.get('fast_alpr'):
        _LOGGER.info(f"Using fast alpr")
    if config.get('code_project'):
        _LOGGER.info(f"Using CodeProject.AI API")
    else:
        _LOGGER.info("No detector configured")


    executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)
    run_mqtt_client()


if __name__ == '__main__':
    main()
