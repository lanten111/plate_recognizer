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
class CameraConfig:
    zones: List[str] = field(default_factory=list)

@dataclass
class PlateRecogniserConfig:
    watched_plates: List[WatchedPlate] = field(default_factory=list)
    fuzzy_match: Optional[float] = None
    save_snapshots: Optional[bool] = None
    frigate_url: Optional[str] = None
    mqtt_server: Optional[str] = None
    mqtt_port: Optional[int] = None
    mqtt_username: Optional[str] = None
    mqtt_password: Optional[str] = None
    db_path: Optional[str] = None
    log_file_path: Optional[str] = None
    snapshot_path: Optional[str] = None
    config_dir: Optional[str] = None
    executor: Optional[any] = None
    main_topic: Optional[str] = None
    return_topic: Optional[str] = None
    discovery_topic: Optional[str] = None
    days_to_keep_images_in_days: Optional[int] = None
    watched_binary_sensor_reset_in_sec: Optional[int] = None
    camera: Dict[str, CameraConfig] = field(default_factory=dict)
    objects: List[str] = field(default_factory=list)
    min_score: Optional[float] = None

@dataclass
class FastAlprConfig:
    plate_detector_model: Optional[str] = None
    ocr_model: Optional[str] = None

@dataclass
class Config:
    plate_recogniser: PlateRecogniserConfig = field(default_factory=PlateRecogniserConfig)
    fast_alpr: FastAlprConfig = field(default_factory=FastAlprConfig)
    logger_level: Optional[str] = None

def get_Config() -> Config:
    LOCAL = os.getenv('LOCAL', 'True').lower() == 'true'
    if LOCAL:
        config_dir =  "config"  # Default to a local directory if not provided
    else:
        config_dir = "/config"  # Default to a production directory if not LOCAL

    data = yaml.safe_load(config_dir + "/" + "config.yml")

    # Convert watched plates to WatchedPlate objects
    watched_plates = [
        WatchedPlate(
            number=plate.get('number'),
            owner=plate.get('owner'),
            car_brand=plate.get('car_brand')
        ) for plate in data.get('plate_recogniser', {}).get('watched_plates', [])
    ]

    # Convert camera zones to CameraConfig objects
    camera = {
        name: CameraConfig(zones=cfg.get('zones', []))
        for name, cfg in data.get('plate_recogniser', {}).get('camera', {}).items()
    }


    # Function to resolve and prepend config_dir if necessary
    def resolve_path(path: str) -> str:
        if path and not path.startswith("/"):  # If path is a relative path (filename)
            return os.path.join(config_dir, path)  # Prepend config_dir to the filename
        return path  # Return the path if it's already absolute

    db_path = resolve_path(data.get('plate_recogniser', {}).get('db_path', 'plate_recogniser.db'))
    log_file_path = resolve_path(data.get('plate_recogniser', {}).get('log_file_path', 'late_recogniser.log'))
    snapshot_path = resolve_path(data.get('plate_recogniser', {}).get('snapshot_path', 'snapshots'))
    executor = ThreadPoolExecutor(max_workers=20)
    # Construct PlateRecogniserConfig
    plate_recogniser = PlateRecogniserConfig(
        watched_plates=watched_plates,
        fuzzy_match=data.get('plate_recogniser', {}).get('fuzzy_match'),
        save_snapshots=data.get('plate_recogniser', {}).get('save_snapshots'),
        frigate_url=data.get('plate_recogniser', {}).get('frigate_url'),
        config_dir=config_dir,
        db_path=db_path,
        log_file_path=log_file_path,
        snapshot_path=snapshot_path,
        executor=executor,
        mqtt_server=data.get('plate_recogniser', {}).get('mqtt_server'),
        mqtt_port=data.get('plate_recogniser', {}).get('mqtt_port'),
        mqtt_username=data.get('plate_recogniser', {}).get('mqtt_username'),
        mqtt_password=data.get('plate_recogniser', {}).get('mqtt_password'),
        main_topic=data.get('plate_recogniser', {}).get('main_topic'),
        return_topic=data.get('plate_recogniser', {}).get('return_topic'),
        discovery_topic=data.get('plate_recogniser', {}).get('discovery_topic'),
        days_to_keep_images_in_days=data.get('plate_recogniser', {}).get('days_to_keep_images_in_days'),
        watched_binary_sensor_reset_in_sec=data.get('plate_recogniser', {}).get('watched_binary_sensor_reset_in_sec'),
        camera=camera,
        objects=data.get('plate_recogniser', {}).get('objects', []),
        min_score=data.get('plate_recogniser', {}).get('min_score')
    )

    # Construct FastAlprConfig
    fast_alpr = FastAlprConfig(
        plate_detector_model=data.get('fast_alpr', {}).get('plate_detector_model'),
        ocr_model=data.get('fast_alpr', {}).get('ocr_model')
    )

    # Construct the final Config object
    return Config(
        plate_recogniser=plate_recogniser,
        fast_alpr=fast_alpr,
        logger_level=data.get('logger_level')
    )
