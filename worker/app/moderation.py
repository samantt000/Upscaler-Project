"""Модерация контента (FR-007).

Заглушка: пропускает все изображения. Точка расширения для классификатора
запрещённого контента; до его подключения сервис не открывается публично.
"""
from dataclasses import dataclass

import numpy as np

ENABLED = False


@dataclass
class ModerationResult:
    allowed: bool
    reason: str = ""


def check(img_rgb: np.ndarray) -> ModerationResult:
    return ModerationResult(allowed=True)
