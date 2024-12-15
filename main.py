#!/bin/python3

import concurrent.futures
import os
import time
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
import yaml
import sys
import difflib

from modules.alpr import get_alpr, fast_alpr
from modules.config import get_Config
from modules.database import setup_db, select_from_table
from modules.frigate import get_latest_snapshot
from modules.mqtt import send_mqtt_message, run_mqtt_client

VERSION = '2.1.1'

DATETIME_FORMAT = "%Y-%m-%d_%H-%M-%S"

DEFAULT_OBJECTS = ['car', 'motorcycle', 'bus']
CURRENT_EVENTS = {}

event_type = None



def process_plate_detection(config, camera_name, frigate_event_id):
    timestamp = datetime.now()
    print(f"{timestamp} start processing event {frigate_event_id}")
    snapshot = get_latest_snapshot(config, frigate_event_id, camera_name)

    if not is_plate_found_for_event(config, frigate_event_id):
        detected_plate_number, detected_plate_score = get_plate(snapshot)
        watched_plate, fuzzy_score, owner, car_brand, watched_plate_numbers = check_watched_plates(detected_plate_number)

        if watched_plate is not None and fuzzy_score is not None:
            print(f"{datetime.now()} storing plate({detected_plate_number}) in db")
            store_plate_in_db(None, detected_plate_number, round(fuzzy_score, 2), frigate_event_id, camera_name, watched_plate, True)
            print(f"{datetime.now()} saving  plate({detected_plate_number}) image")
            image_path = save_image(config, detected_plate_score, snapshot, camera_name, detected_plate_number)
            print(f"{datetime.now()} sending mqtt message for  plate({detected_plate_number})")
            send_mqtt_message(config, detected_plate_number, detected_plate_score, frigate_event_id, camera_name, watched_plate, watched_plate_numbers, owner, car_brand, fuzzy_score, image_path)
            executor.submit(delete_old_files)
            print(f"plate({detected_plate_number}) match found in watched plates ({watched_plate}) for event {frigate_event_id}, {event_type} stops")
    else:
        print(f"plate already found for event {frigate_event_id}, {event_type} skipping........")



def get_vehicle_direction(config,  after_data, frigate_event_id):
    direction = get_db_event_direction(config, frigate_event_id)
    if direction is None and len(after_data['current_zones']) > 0:
            current_zone = after_data['current_zones'][0]
            cameras = config['frigate'].get('camera', [])
            for camera in cameras:
                if camera == after_data['camera']:
                    zones = cameras.get(camera, {}).get('zones', [])
                    if current_zone == zones[0]:
                        store_vehicle_direction_in_db("entering", frigate_event_id)
                    else:
                        store_vehicle_direction_in_db("exiting", frigate_event_id)
    else:
        print(f"event  {frigate_event_id} vehicle direction exit as {direction}.")



def check_watched_plates(plate_number):
    config_watched_plates = config['frigate'].get('watched_plates', [])
    watched_plate_numbers  = [plate['number'] for plate in config_watched_plates]
    if not watched_plate_numbers:
        _LOGGER.debug("Skipping checking Watched Plates because watched_plates is not set")
        return None, None, None

    watched_plate_numbers = [str(x).lower() for x in watched_plate_numbers] #make sure watched_plates are all lower case
    
    #Step 1 - test if top plate is a watched plate
    matching_plate = str(plate_number).lower() in watched_plate_numbers
    if matching_plate:
        _LOGGER.info(f"Recognised plate is a Watched Plate: {plate_number}")
        return None, None, None  

    fuzzy_match = config['frigate'].get('fuzzy_match', 0) 
    
    if fuzzy_match == 0:
        _LOGGER.debug(f"Skipping fuzzy matching because fuzzy_match value not set in config")
        return None, None, None

    best_match, max_score = None, 0
    owner, car_brand = None, None
    for watched_plate in watched_plate_numbers:
        seq = difflib.SequenceMatcher(a=str(plate_number).lower(), b=str(watched_plate).lower())
        if seq.ratio() > max_score: 
            max_score = seq.ratio()
            best_match = watched_plate
    
    _LOGGER.debug(f"Best fuzzy_match: {best_match} ({max_score})")

    if max_score >= fuzzy_match:
        _LOGGER.info(f"Watched plate found from fuzzy matching: {best_match} with score {max_score}")
        for plate in config_watched_plates:
            if plate['number'].lower() == best_match:
                owner = plate['owner']
                car_brand = plate['car_brand']
                break
        return best_match, max_score, owner, car_brand, watched_plate_numbers


    return None, None
    


def save_image(config, plate_score, snapshot, camera_name, plate_number):

    timestamp = datetime.now().strftime(DATETIME_FORMAT)
    image_name = f"{camera_name}_{timestamp}.png"
    if plate_number:
        image_name = f"{str(plate_number).upper()}_{int(plate_score* 100)}%_{image_name}"
    image_path = f"{SNAPSHOT_PATH}/{image_name}"

    image_array = np.frombuffer(snapshot, np.uint8)
    frame = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    annotated_frame = get_alpr().draw_predictions(frame)
    cv2.imwrite(image_path, annotated_frame)

    _LOGGER.info(f"Saving image with path: {image_path}")
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


def get_plate(snapshot):
    if config.get('fast_alpr'):
        detected_plate_number, detected_plate_score = fast_alpr(snapshot)
    else:
        _LOGGER.error("Plate Recognizer is not configured")
        return None, None, None, None

    return detected_plate_number, detected_plate_score

def is_plate_found_for_event(config, frigate_event_id):

    results = select_from_table(config.plate_recogniser.db_path, "plates", "plate_found",  'frigate_event_id = ?', frigate_event_id )
    return bool(results.get('plate_found')) if results else False

def get_db_event_direction(config, frigate_event_id):
    results = select_from_table(config.plate_recogniser.db_path , "plates", "vehicle_direction",  'frigate_event_id = ?', frigate_event_id )
    return results

def load_config():
    global config
    with open(CONFIG_PATH, 'r') as config_file:
        config = yaml.safe_load(config_file)

    if SNAPSHOT_PATH and not os.path.isdir(SNAPSHOT_PATH):
        os.makedirs(SNAPSHOT_PATH)


def delete_old_files():

    folder_path = SNAPSHOT_PATH
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

def load_logger(config):
    global _LOGGER
    _LOGGER = logging.getLogger(__name__)
    _LOGGER.setLevel(config.plate_recogniser.logger_level)
    # Create a formatter to customize the log message format
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Create a console handler and set the level to display all messages
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formatter)

    # Create a file handler to log messages to a file
    file_handler = logging.FileHandler(config.plate_recogniser.log_file_path)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    # Add the handlers to the logger
    _LOGGER.addHandler(console_handler)
    _LOGGER.addHandler(file_handler)

def main():

    executor = ThreadPoolExecutor(max_workers=20)

    config = get_Config()

    load_config(config)
    setup_db(config)
    load_logger(config)

    os.makedirs(config.plate_recogniser.snapshot_path, exist_ok=True)

    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    _LOGGER.info(f"Time: {current_time}")
    _LOGGER.info(f"Python Version: {sys.version}")
    _LOGGER.info(f"Frigate Plate Recognizer Version: {VERSION}")
    _LOGGER.debug(f"config: {config}")

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)
    run_mqtt_client(config, _LOGGER, executor, DATETIME_FORMAT)


if __name__ == '__main__':
    main()
