import os
from concurrent.futures import ThreadPoolExecutor

import yaml
from dataclasses import dataclass, field
from typing import List, Dict, Optional

@dataclass
class WatchedPlate:
    number: Optional[str] = None
    owner: Optional[str] = None
    car_brand: Optional[str] = None

@dataclass
class CameraDirectionConfig:
    first_zone: Optional[str] = None
    last_zone: Optional[str] = None

@dataclass
class CameraConfig:
    direction: Optional[CameraDirectionConfig] = None
    trigger_zones: List[str] = field(default_factory=list)

@dataclass
class FastAlprConfig:
    plate_detector_model: Optional[str] = None
    ocr_model: Optional[str] = None

@dataclass
class Config:
    watched_plates: List[WatchedPlate] = field(default_factory=list)
    fuzzy_match: Optional[float] = None
    save_snapshots: Optional[bool] = False
    frigate_url: Optional[str] = None
    mqtt_server: Optional[str] = None
    mqtt_port: Optional[int] = 1883
    mqtt_username: Optional[str] = None
    mqtt_password: Optional[str] = None
    db_path: Optional[str] = None
    log_file_path: Optional[str] = None
    snapshot_path: Optional[str] = None
    debug_snapshot_path: Optional[str] = None
    date_format: Optional[str] = None
    config_dir: Optional[str] = None
    table: Optional[str] = None
    manufacturer: Optional[str] = "Plate Detection"
    executor: Optional[any] = None
    default_objects: List = None
    main_topic: Optional[str] = None
    return_topic: Optional[str] = None
    discovery_topic: Optional[str] = None
    days_to_keep_images_in_days: Optional[int] = None
    binary_sensor_reset_in_sec: Optional[int] = 10
    camera: Dict[str, CameraConfig] = field(default_factory=dict)
    objects: List[str] = field(default_factory=list)
    min_score: Optional[float] = None
    fast_alpr: FastAlprConfig = field(default_factory=FastAlprConfig)
    logger_level: Optional[str] = None

def get_yaml_config() -> Config:
    local = os.getenv('LOCAL', 'True').lower() == 'true'
    config_dir = "config" if local else "/config"

    # Load YAML content
    config_path = os.path.join(config_dir, "config.yml")
    with open(config_path, 'r') as file:
        data = yaml.safe_load(file) or {}

    # Helper to resolve paths
    def resolve_path(path: str) -> str:
        if path and not path.startswith("/"):
            return os.path.join(config_dir, path)
        return path

    # Process fields with defaults
    watched_plates = [
        WatchedPlate(
            number=plate.get('number'),
            owner=plate.get('owner'),
            car_brand=plate.get('car_brand')
        ) for plate in data.get('plate_recogniser', {}).get('watched_plates', [])
    ]

    camera = {
        name: CameraConfig(
            direction=CameraDirectionConfig(
                first_zone=cfg.get('direction', {}).get('first_zone'),
                last_zone=cfg.get('direction', {}).get('last_zone')
            ),
            trigger_zones=cfg.get('trigger_zones', [])
        ) for name, cfg in data.get('plate_recogniser', {}).get('camera', {}).items()
    }

    db_path = resolve_path(data.get('plate_recogniser', {}).get('db_path', 'plate_recogniser.db'))
    log_file_path = resolve_path(data.get('plate_recogniser', {}).get('log_file_path', 'late_recogniser.log'))
    debug_snapshot_path = resolve_path(data.get('plate_recogniser', {}).get('debug_snapshot_path', 'debug_snapshot'))
    snapshot_path = resolve_path(data.get('plate_recogniser', {}).get('snapshot_path', 'snapshots'))

    executor = ThreadPoolExecutor(max_workers=50)
    date_format = "%Y-%m-%d_%H-%M-%S"
    default_objects = ['car', 'motorcycle', 'bus']
    table = "plates"

    # Construct and return Config object
    return Config(
        watched_plates=watched_plates,
        fuzzy_match=data.get('plate_recogniser', {}).get('fuzzy_match'),
        save_snapshots=data.get('plate_recogniser', {}).get('save_snapshots'),
        frigate_url=data.get('plate_recogniser', {}).get('frigate_url'),
        config_dir=config_dir,
        db_path=db_path,
        log_file_path=log_file_path,
        snapshot_path=snapshot_path,
        executor=executor,
        date_format=date_format,
        default_objects=default_objects,
        table=table,
        debug_snapshot_path=debug_snapshot_path,
        mqtt_server=data.get('plate_recogniser', {}).get('mqtt_server'),
        manufacturer=data.get('plate_recogniser', {}).get('manufacturer'),
        mqtt_port=data.get('plate_recogniser', {}).get('mqtt_port'),
        mqtt_username=data.get('plate_recogniser', {}).get('mqtt_username'),
        mqtt_password=data.get('plate_recogniser', {}).get('mqtt_password'),
        main_topic=data.get('plate_recogniser', {}).get('main_topic'),
        return_topic=data.get('plate_recogniser', {}).get('return_topic'),
        discovery_topic=data.get('plate_recogniser', {}).get('discovery_topic'),
        days_to_keep_images_in_days=data.get('plate_recogniser', {}).get('days_to_keep_images_in_days'),
        binary_sensor_reset_in_sec=data.get('plate_recogniser', {}).get('binary_sensor_reset_in_sec'),
        camera=camera,
        objects=data.get('plate_recogniser', {}).get('objects', []),
        min_score=data.get('plate_recogniser', {}).get('min_score'),
        fast_alpr=FastAlprConfig(
            plate_detector_model=data.get('plate_recogniser', {}).get('fast_alpr', {}).get('plate_detector_model'),
            ocr_model=data.get('plate_recogniser', {}).get('fast_alpr', {}).get('ocr_model')
        ),
        logger_level=data.get('plate_recogniser', {}).get('logger_level')
    )
