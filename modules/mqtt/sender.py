import base64
import json
import time
from dataclasses import asdict

from modules.database import get_plate


def send_mqtt_message(config, frigate_event_id,  mqtt_client, logger):
    logger.info(f"sending mqtt message for  event ({frigate_event_id}")
    payload = get_plate(config, frigate_event_id, logger)
    if payload:
        payload = payload[0]
    else:
        logger.error(f"Bad payload supplied")
        return
    def watched_plates_to_json(watched_plates) -> str:
        plates_as_dicts = [asdict(plate) for plate in watched_plates]
        return json.dumps(plates_as_dicts, indent=4)

    vehicle_data = {
        'fuzzy_score': payload.get('fuzzy_score'),
        'is_watched_plate_matched': bool(payload.get('is_watched_plate_matched')),
        'is_trigger_zone_reached': bool(payload.get('is_trigger_zone_reached')),
        'detected_plate': str(payload.get('detected_plate')).upper() ,
        # 'watched_plates': watched_plates_to_json(payload.get('watched_plates')) ,
        'matched_watched_plate': payload.get('matched_watched_plate') ,
        'entered_zones': payload.get('entered_zones'),
        'trigger_zones':  payload.get('trigger_zones'),
        'frigate_event_id':  payload.get('frigate_event_id'),
        'camera_name': payload.get('camera_name'),
        "plate_image":  payload.get('image_path'),
        'vehicle_direction': payload.get('vehicle_direction'),
        "vehicle_owner":  payload.get('vehicle_owner'),
        "vehicle_brand":  payload.get('vehicle_brand')

    }

    def encode_image_to_base64(image_path):
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")
    vehicle_data['plate_image'] = encode_image_to_base64(payload.get('image_path'))

    device_config = {
        "name": f"{payload.get('camera_name')} Plate Detection",
        "identifiers": "License Plate Detection",
        "manufacturer": config.manufacturer,
        "sw_version": "1.0"
    }

    for key, value in vehicle_data.items():
        if key == "is_watched_plate_matched" or key == "is_trigger_zone_reached":
            # Binary Sensor Configuration
            discovery_topic = f"homeassistant/binary_sensor/vehicle_data/{key}/config"
            state_topic = f"homeassistant/binary_sensor/vehicle_data/{key}/state"

            payload = {
                "name": f"{key.replace('_', ' ').title()}",
                "state_topic": state_topic,
                "payload_on": "True",
                "payload_off": "False",
                "device_class": "motion",
                "unique_id": f"vehicle_binary_sensor_{key}",
                "device": device_config
            }
            config.executor.submit(publish_message,config, discovery_topic, state_topic, payload, value, mqtt_client, logger)
            config.executor.submit(reset_binary_sensor_state_after_delay,config, state_topic, config.binary_sensor_reset_in_sec, False, mqtt_client, logger)


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
    # logger.info(f"successful sent detected plate to mqtt {discovery_topic}")

def reset_binary_sensor_state_after_delay(config, state_topic, delay, value, mqtt_client, logger):
    time.sleep(delay)
    mqtt_client.publish(state_topic, value , retain=True)
    logger.info(f"Binary sensor state set to OFF after {delay} seconds.")