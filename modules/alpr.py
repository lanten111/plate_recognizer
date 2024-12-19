import cv2
import numpy as np
from fast_alpr import ALPR

from modules.logger import setup_logger
from modules.config import Config

logger = setup_logger(__name__)

def get_alpr(config:Config):
    plate_detector_model = config.fast_alpr.plate_detector_model
    ocr_model = config.fast_alpr.ocr_model
    return ALPR(
        detector_model=plate_detector_model,
        ocr_model=ocr_model,ocr_device="cpu"
    )

def fast_alpr(config, snapshot):
    image_array = np.frombuffer(snapshot, np.uint8)
    frame = cv2.imdecode(image_array, cv2.IMREAD_COLOR)

    alpr_results = get_alpr(config).predict(frame)

    logger.info(f"detection results {alpr_results}")

    for result in alpr_results:
        ocr_text = result.ocr.text
        ocr_confidence = result.ocr.confidence
        return ocr_text, ocr_confidence

    return None, None