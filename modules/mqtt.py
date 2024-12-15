import time
import paho.mqtt.client as mqtt
from modules.frigate import process_message


def run_mqtt_client(config, logger):

    logger.info(f"Starting MQTT client. Connecting to: {config.mqtt_server}")

    mqtt_client = get_mqtt_client()
    param_data = {
        "logger": logger,
        "config": config,
    }
    mqtt_client.user_data_set(param_data)
    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client.on_message = on_message

    if config.mqtt_username is not None and config.mqtt_password is not None:
        mqtt_client.username_pw_set(config.mqtt_username, config.mqtt_password)

    mqtt_client.connect(config.mqtt_server, config.mqtt_port)
    mqtt_client.loop_forever()

def on_message(mqtt_client, userdata, message):
    logger = userdata.get("logger")
    config = userdata.get("config")
    process_message(config, message, logger)

def on_connect(mqtt_client, userdata, flags, reason_code, properties):
    logger = userdata.get("logger")
    config = userdata.get("config")

    logger.info("MQTT Connected")
    mqtt_client.subscribe(config.main_topic + "/events")

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

def get_mqtt_client() -> mqtt:
    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    mqtt_client.enable_logger()
    return  mqtt_client