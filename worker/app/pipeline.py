"""Конвейер обработки одной задачи: модерация → лица → апскейл → контроль качества."""
import os
import time
from dataclasses import dataclass
from typing import Callable

import cv2
import numpy as np
from PIL import Image, ImageOps

from . import config, moderation
from .faces import FaceDetector, face_mask
from .quality import Brisque, detail_index, diff_heatmap, diff_map, masked_mean
from .upscaler import Upscaler

SMALL_INPUT_SIDE = 64
HIGH_DETAIL_INDEX = 0.35

SAVE_PARAMS = {
    "jpg": ("JPEG", {"quality": 95}),
    "png": ("PNG", {"compress_level": 3}),
    "webp": ("WEBP", {"quality": 95}),
}


class Rejected(Exception):
    """Изображение отклонено до обработки (например, модерацией)."""


@dataclass
class Models:
    upscaler: Upscaler
    faces: FaceDetector
    brisque: Brisque

    @classmethod
    def load(cls) -> "Models":
        return cls(Upscaler(), FaceDetector(), Brisque())


def load_rgb(path: str) -> tuple[np.ndarray, bool]:
    """Читает изображение с учётом EXIF-ориентации; прозрачность заменяется белым фоном."""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        had_alpha = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
        if had_alpha:
            rgba = im.convert("RGBA")
            background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            im = Image.alpha_composite(background, rgba)
        return np.asarray(im.convert("RGB")), had_alpha


def soften_faces(result: np.ndarray, bicubic: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """FR-006: в области лиц приближает результат к бикубическому апскейлу."""
    ys, xs = np.nonzero(mask > 1e-3)
    if len(ys) == 0:
        return result
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    k = (mask[y0:y1, x0:x1] * config.FACE_DETAIL_REDUCTION)[..., None]
    sr = result[y0:y1, x0:x1].astype(np.float32)
    bc = bicubic[y0:y1, x0:x1].astype(np.float32)
    out = result.copy()
    out[y0:y1, x0:x1] = np.clip(sr - k * (sr - bc), 0, 255).round().astype(np.uint8)
    return out


def preview_size(w: int, h: int) -> tuple[int, int]:
    k = min(1.0, config.PREVIEW_MAX_SIDE / max(w, h))
    return max(1, round(w * k)), max(1, round(h * k))


def save_jpeg(path: str, img_rgb: np.ndarray) -> None:
    Image.fromarray(img_rgb).save(path, "JPEG", quality=88)


def process(
    job_dir: str, ext: str, scale: int, models: Models, progress: Callable[[float], None]
) -> dict:
    t_start = time.perf_counter()
    img, had_alpha = load_rgb(os.path.join(job_dir, f"input.{ext}"))
    h, w = img.shape[:2]
    warnings: list[str] = []

    verdict = moderation.check(img)
    if not verdict.allowed:
        raise Rejected(verdict.reason or "Изображение не прошло проверку контента")
    progress(0.02)

    faces = models.faces.detect(img)
    progress(0.05)

    t_model = time.perf_counter()
    result = models.upscaler.upscale(img, scale, lambda p: progress(0.05 + 0.80 * p))
    model_seconds = time.perf_counter() - t_model
    out_h, out_w = result.shape[:2]

    bicubic = cv2.resize(img, (out_w, out_h), interpolation=cv2.INTER_CUBIC)
    face_stats = {}
    flag_face_zone = False
    if faces:
        mask = face_mask(faces, (out_h, out_w), scale)
        before = masked_mean(diff_map(result, bicubic), mask)
        result = soften_faces(result, bicubic, mask)
        diff = diff_map(result, bicubic)
        after = masked_mean(diff, mask)
        face_index = detail_index(diff, mask)
        flag_face_zone = face_index > config.FACE_ZONE_INDEX_THRESHOLD
        face_stats = {
            "face_diff_before": round(before, 3),
            "face_diff_after": round(after, 3),
            "face_diff_reduction": round(1 - after / before, 4) if before > 0 else 0.0,
            "face_detail_index": round(face_index, 4),
        }
        warnings.append(
            f"Обнаружено лиц: {len(faces)}. В их области достройка деталей снижена, "
            "чтобы не искажать черты лица."
        )
        if flag_face_zone:
            warnings.append(
                "Зона активной генерации деталей: в области лица модель заметно изменила "
                "изображение. Не используйте результат как документ или доказательство."
            )
    else:
        diff = diff_map(result, bicubic)
    del bicubic
    progress(0.90)

    index = detail_index(diff)
    if index > HIGH_DETAIL_INDEX:
        warnings.append(
            "Высокий индекс достройки: значительная часть мелких деталей сгенерирована моделью."
        )
    if min(h, w) < SMALL_INPUT_SIDE:
        warnings.append(
            "Исходное изображение меньше 64 px по одной из сторон: большая часть деталей "
            "достроена моделью и может не соответствовать оригиналу."
        )
    if had_alpha:
        warnings.append("Прозрачность не поддерживается: прозрачный фон заменён белым.")

    brisque = models.brisque.score(result)

    fmt, params = SAVE_PARAMS[ext]
    Image.fromarray(result).save(os.path.join(job_dir, f"result.{ext}"), fmt, **params)
    pw, ph = preview_size(out_w, out_h)
    save_jpeg(os.path.join(job_dir, "preview_before.jpg"), cv2.resize(img, (pw, ph), interpolation=cv2.INTER_CUBIC))
    save_jpeg(os.path.join(job_dir, "preview_after.jpg"), cv2.resize(result, (pw, ph), interpolation=cv2.INTER_AREA))
    save_jpeg(os.path.join(job_dir, "preview_diff.jpg"), diff_heatmap(cv2.resize(diff, (pw, ph), interpolation=cv2.INTER_AREA)))
    progress(1.0)

    return {
        "width": w,
        "height": h,
        "out_width": out_w,
        "out_height": out_h,
        "faces": len(faces),
        "flag_face_zone": flag_face_zone,
        "detail_index": round(index, 4),
        "brisque": round(brisque, 3),
        "model_seconds": round(model_seconds, 3),
        "total_seconds": round(time.perf_counter() - t_start, 3),
        "warnings": warnings,
        **face_stats,
    }
