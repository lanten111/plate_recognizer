import json
import time
from datetime import datetime

from main import begin_process, get_vehicle_direction
from modules.database import select_from_table


def process_message(config,  message, logger, executor):

    global event_type
    payload_dict = json.loads(message.payload)
    logger.debug(f"MQTT message: {payload_dict}")

    before_data = payload_dict.get("before", {})
    after_data = payload_dict.get("after", {})
    event_type = payload_dict.get("type", "")
    frigate_event_id = after_data["id"]

    if is_invalid_event(config, before_data, after_data):
        return

    if is_duplicate_event(config, frigate_event_id):
        return

    executor.submit(config, get_vehicle_direction, after_data, frigate_event_id)

    if event_type == "new":
        print(f"Starting new thread for new event{event_type} for {frigate_event_id}***************")
        executor.submit(config, begin_process, after_data, frigate_event_id)

def begin_process(config, after_data, frigate_event_id):
    global event_type
    loop = 0
    while event_type in ["update", "new"] and not is_plate_found_for_event(config, frigate_event_id):
        loop=loop + 1
        timestamp = datetime.now()
        print(f"{timestamp} start processing loop {loop} for {frigate_event_id}")
        config.plate_recogniser.exexecutor.submit(process_plate_detection ,config,  after_data['camera'], frigate_event_id)
        time.sleep(0.5)

    print(f"Done processing event {frigate_event_id}, {event_type}")


def get_latest_snapshot(config, frigate_event_id, camera_name):
    timestamp = datetime.now()
    start_time = time.time()
    print(f"*********{timestamp} Getting snapshot for event: {frigate_event_id}")
    snapshot_url = f"{config.plate_recogniser.}/api/{camera_name}/latest.jpg"

    _LOGGER.debug(f"event URL: {snapshot_url}")

    parameters = {"quality": 100}
    response = requests.get(snapshot_url, params=parameters)
    snapshot = response.content
    print(f"*********{timestamp} done snapshot for event: {frigate_event_id}")
    end_time = time.time()
    duration = end_time - start_time
    print(f"*********The process took {duration:.2f} seconds to complete.")
    return snapshot


def is_invalid_event(config, after_data):
    # check if it is from the correct camera or zone
    config_zones = config['frigate'].get('zones', [])
    config_cameras = list(config['frigate'].get('camera', {}).keys())

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

def is_duplicate_event(config, frigate_event_id):
    results = select_from_table(DB_PATH, "plates", "*",  'frigate_event_id = ?', frigate_event_id )
    return True if results else False

def get_snapshot(config, frigate_event_id, cropped, camera_name):
    _LOGGER.debug(f"Getting snapshot for event: {frigate_event_id}, Crop: {cropped}")
    snapshot_url = f"{frigate_url}/api/events/{frigate_event_id}/snapshot-clean.png"
    _LOGGER.debug(f"event URL: {snapshot_url}")

    parameters = {"crop": 1 if cropped else 0, "quality": 100}
    response = requests.get(snapshot_url, params=parameters)
    snapshot = response.content
    thread = threading.Thread(
        target=save_snap,
        args=(snapshot, camera_name),
        daemon=True,
    )
    thread.start()
    if response.status_code != 200:
        _LOGGER.error(f"Error getting snapshot: {response.status_code}")
        return

    return snapshot