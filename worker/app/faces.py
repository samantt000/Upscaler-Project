"""Детекция лиц (YuNet, OpenCV) и маска для снижения достройки (FR-006)."""
from dataclasses import dataclass

import cv2
import numpy as np

from . import config

# YuNet работает быстрее и точнее на уменьшенной копии
DETECT_MAX_SIDE = 1024


@dataclass
class Face:
    x: int
    y: int
    w: int
    h: int
    score: float


class FaceDetector:
    def __init__(self, model_path: str = config.FACE_MODEL_FILE):
        self.detector = cv2.FaceDetectorYN.create(
            model_path, "", (320, 320), config.FACE_SCORE_THRESHOLD, 0.3, 5000
        )

    def detect(self, img_rgb: np.ndarray) -> list[Face]:
        h, w = img_rgb.shape[:2]
        k = min(1.0, DETECT_MAX_SIDE / max(h, w))
        small = cv2.resize(img_rgb, (round(w * k), round(h * k))) if k < 1 else img_rgb
        bgr = cv2.cvtColor(small, cv2.COLOR_RGB2BGR)
        self.detector.setInputSize((bgr.shape[1], bgr.shape[0]))
        _, found = self.detector.detect(bgr)
        if found is None:
            return []
        return [
            Face(int(f[0] / k), int(f[1] / k), int(f[2] / k), int(f[3] / k), float(f[14]))
            for f in found
        ]


MASK_MAX = 255
MASK_HALF = 128  # граница «внутри лица» (0,5 в долях маски)


def face_mask(faces: list[Face], shape: tuple[int, int], scale: int) -> np.ndarray:
    """Маска 0..255 (uint8) размера выхода: эллипсы по лицам с размытыми краями.

    uint8 вместо float32: для выхода 8000×8000 это 64 МБ вместо 256 МБ, и размытие
    создаёт копию того же размера (запись 9 журнала решений).
    """
    h, w = shape
    mask = np.zeros((h, w), dtype=np.uint8)
    for f in faces:
        cx, cy = (f.x + f.w / 2) * scale, (f.y + f.h / 2) * scale
        # Немного расширяем рамку детектора: она не включает лоб и подбородок целиком
        axes = (max(1, int(f.w * scale * 0.65)), max(1, int(f.h * scale * 0.75)))
        cv2.ellipse(mask, (int(cx), int(cy)), axes, 0, 0, 360, MASK_MAX, -1)
    if faces:
        k = max(3, int(min(f.w for f in faces) * scale * 0.15)) | 1
        cv2.GaussianBlur(mask, (k, k), 0, dst=mask)
    return mask
