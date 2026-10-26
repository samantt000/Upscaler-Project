"""Карта изменений, индекс достройки (FR-004, FR-006) и BRISQUE (FR-012)."""
import cv2
import numpy as np

from . import config

BRISQUE_MAX_SIDE = 1024


def diff_map(result: np.ndarray, bicubic: np.ndarray) -> np.ndarray:
    """Модуль разности результата и бикубического апскейла, усреднённый по каналам (uint8, 0–255).

    uint8 вместо float: при выходе 8000×8000 px это экономит сотни мегабайт памяти.
    """
    d = cv2.absdiff(result, bicubic)
    return (d.sum(axis=2, dtype=np.uint16) // 3).astype(np.uint8)


def detail_index(diff: np.ndarray, mask: np.ndarray | None = None) -> float:
    """Доля пикселей, где модель заметно отклонилась от бикубического апскейла."""
    above = diff > config.DIFF_THRESHOLD
    if mask is None:
        return float(above.mean())
    weight = mask > 0.5
    return float(above[weight].mean()) if weight.any() else 0.0


def masked_mean(diff: np.ndarray, mask: np.ndarray) -> float:
    weight = mask > 0.5
    return float(diff[weight].mean()) if weight.any() else 0.0


def diff_heatmap(diff: np.ndarray) -> np.ndarray:
    """Цветная карта изменений (RGB) для отображения в интерфейсе."""
    norm = cv2.convertScaleAbs(diff, alpha=255.0 / 48.0)
    return cv2.cvtColor(cv2.applyColorMap(norm, cv2.COLORMAP_INFERNO), cv2.COLOR_BGR2RGB)


class Brisque:
    def __init__(self):
        self.quality = cv2.quality.QualityBRISQUE_create(
            config.BRISQUE_MODEL_FILE, config.BRISQUE_RANGE_FILE
        )

    def score(self, img_rgb: np.ndarray) -> float:
        """BRISQUE (меньше — лучше). Считается на уменьшенной копии ради скорости."""
        h, w = img_rgb.shape[:2]
        k = min(1.0, BRISQUE_MAX_SIDE / max(h, w))
        if k < 1:
            img_rgb = cv2.resize(img_rgb, (round(w * k), round(h * k)), interpolation=cv2.INTER_AREA)
        return float(self.quality.compute(cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR))[0])


class DriftMonitor:
    """Сравнивает среднее BRISQUE за сеанс работы воркера с базовой линией набора C."""

    def __init__(self, baseline: float | None = config.BRISQUE_BASELINE):
        self.baseline = baseline
        self.total = 0.0
        self.count = 0

    def add(self, value: float) -> dict:
        self.total += value
        self.count += 1
        mean = self.total / self.count
        state = {"session_mean": round(mean, 3), "session_count": self.count}
        if self.baseline:
            deviation = (mean - self.baseline) / self.baseline
            state["baseline"] = self.baseline
            state["deviation"] = round(deviation, 4)
            state["drift"] = abs(deviation) > config.BRISQUE_DRIFT_THRESHOLD
        return state
