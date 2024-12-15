import base64
import json
import time
from datetime import datetime

from paho import mqtt

from modules.frigate import process_message

mqtt_client = None

def run_mqtt_client(config, logger, executor, date_format):
    global mqtt_client
    _LOGGER = logger
    _LOGGER.info(f"Starting MQTT client. Connecting to: {config['frigate']['mqtt_server']}")

    # setup mqtt client
    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    mqtt_client.enable_logger()
    param_data = {
        "logger": logger,
        "config": config,
        "executor": executor,
    }
    mqtt_client.user_data_set(param_data)
    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client.on_message = on_message

    if config.plate_recogniser.mqtt_username is not None and config.plate_recogniser.mqtt_password is not None:
        mqtt_client.username_pw_set(config.plate_recogniser.mqtt_username, config.plate_recogniser.mqtt_password)

    mqtt_client.connect(config.plate_recogniser.mqtt_server, config.plate_recogniser.mqtt_port)
    mqtt_client.loop_forever()

def on_message(client, userdata, message):
    logger = userdata.get("logger")
    config = userdata.get("config")
    executor = userdata.get("executor")
    process_message(config, message, logger, executor)

def on_connect(mqtt_client, userdata, flags, reason_code, properties):
    logger = userdata.get("logger")
    config = userdata.get("config")

    logger.info("MQTT Connected")
    mqtt_client.subscribe(config.plate_recogniser.main_topic + "/events")

def on_disconnect(mqtt_client, userdata, flags, reason_code, properties):
    logger = userdata.get("logger")
    if reason_code != 0:
        logger.warning(f"Unexpected disconnection, trying to reconnect userdata:{userdata}, flags:{flags}, properties:{properties}")
        while True:
            try:
                mqtt_client.reconnect()
                break
            except Exception as e:
                logger.warning(f"Reconnection failed due to {e}, retrying in 60 seconds")
                time.sleep(60)
    else:
        logger.error("Expected disconnection")

def send_mqtt_message(config, detected_plate_number, plate_score, frigate_event_id, camera_name, watched_plate, watched_plates, owner , car_brand, fuzzy_score, image_path, date_format, executor):
    timestamp = datetime.now().strftime(date_format)
    vehicle_data = {
        'fuzzy_score': round(fuzzy_score,2),
        'matched': False,
        'detected_plate_number': str(detected_plate_number).upper(),
        'detected_plate_ocr_score': round(plate_score,2),
        'frigate_event_id': frigate_event_id,
        'watched_plates': json.dumps(watched_plates),
        'camera_name': camera_name,
        "plate_image": image_path,
        'watched_plate': str(watched_plate).upper(),
        'vehicle_direction': "",
        "vehicle_owner": owner,
        "vehicle_brand": car_brand

    }

    vehicle_data['matched'] = vehicle_data['fuzzy_score'] > 0.8
    vehicle_direction = get_db_event_direction(config, frigate_event_id)
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
            executor.submit(publish_message, discovery_topic, state_topic, payload, value )
            executor.submit(reset_binary_sensor_state_after_delay,state_topic, config.get('frigate').get('watched_binary_sensor_reset_in_sec'), value)


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