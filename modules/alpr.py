import cv2
import numpy as np
from fast_alpr import ALPR

from modules.config import Config


def get_alpr(config:Config, logger):
    plate_detector_model = config.fast_alpr.plate_detector_model
    ocr_model = config.fast_alpr.ocr_model
    return ALPR(
        detector_model=plate_detector_model,
        ocr_model=ocr_model,ocr_device="cpu"
    )

def fast_alpr(config, snapshot, logger):
    image_array = np.frombuffer(snapshot, np.uint8)
    frame = cv2.imdecode(image_array, cv2.IMREAD_COLOR)

    alpr_results = get_alpr(config, logger).predict(frame)

    logger.info(f"detection results {alpr_results}")

    for result in alpr_results:
        ocr_text = result.ocr.text
        ocr_confidence = result.ocr.confidence
        return ocr_text, ocr_confidence

    return None, None