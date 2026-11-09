"""Инференс моделей суперразрешения ×4 с тайловой обработкой (FR-003).

Сервис использует Real-ESRGAN x4plus. Модели SwinIR-M подключены для сравнения
в скрипте оценки (tools.eval_metrics) и как задел для второго режима обработки.
"""
import math
import os
from dataclasses import dataclass
from typing import Callable, Optional

import cv2
import numpy as np
import torch
from torch import nn

from . import config
from .rrdbnet import RRDBNet
from .swinir_arch import SwinIR

MODEL_SCALE = 4


def _swinir(img_size: int, upsampler: str) -> nn.Module:
    return SwinIR(upscale=4, in_chans=3, img_size=img_size, window_size=8, img_range=1.0,
                  depths=[6] * 6, embed_dim=180, num_heads=[6] * 6, mlp_ratio=2,
                  upsampler=upsampler, resi_connection="1conv")


@dataclass(frozen=True)
class ModelSpec:
    title: str
    file: str
    build: Callable[[], nn.Module]
    state_key: str
    # fp16 на GPU: для свёрточной Real-ESRGAN безопасно; SwinIR (трансформер) считаем в fp32
    half: bool
    # Предел размера тайла: внимание в SwinIR требует заметно больше видеопамяти
    max_tile: Optional[int] = None
    tile_pad: int = 10


MODELS: dict[str, ModelSpec] = {
    "realesrgan": ModelSpec(
        title="Real-ESRGAN x4plus", file="RealESRGAN_x4plus.pth",
        build=RRDBNet, state_key="params_ema", half=True),
    "swinir-classical": ModelSpec(
        title="SwinIR-M classical x4", file="001_classicalSR_DIV2K_s48w8_SwinIR-M_x4.pth",
        build=lambda: _swinir(48, "pixelshuffle"), state_key="params", half=False,
        max_tile=256, tile_pad=16),
    "swinir-real": ModelSpec(
        title="SwinIR-M real-world x4 (GAN)", file="003_realSR_BSRGAN_DFO_s64w8_SwinIR-M_x4_GAN.pth",
        build=lambda: _swinir(64, "nearest+conv"), state_key="params_ema", half=False,
        max_tile=256, tile_pad=16),
}
DEFAULT_MODEL = "realesrgan"


def resolve_device() -> torch.device:
    if config.DEVICE == "cuda" or (config.DEVICE == "auto" and torch.cuda.is_available()):
        return torch.device("cuda")
    return torch.device("cpu")


class Upscaler:
    def __init__(self, model: str = DEFAULT_MODEL, device: Optional[torch.device] = None):
        self.spec = MODELS[model]
        self.device = device or resolve_device()
        if self.device.type == "cpu" and config.TORCH_THREADS > 0:
            torch.set_num_threads(config.TORCH_THREADS)
        self.half = self.spec.half and self.device.type == "cuda"
        self.tile = min(config.TILE_SIZE, self.spec.max_tile or config.TILE_SIZE)

        net = self.spec.build()
        state = torch.load(os.path.join(config.MODELS_DIR, self.spec.file), map_location="cpu",
                           weights_only=True)
        net.load_state_dict(state.get(self.spec.state_key, state), strict=True)
        net.eval()
        self.model = net.half().to(self.device) if self.half else net.to(self.device)

    @property
    def title(self) -> str:
        return self.spec.title

    @property
    def device_name(self) -> str:
        if self.device.type == "cuda":
            return f"GPU ({torch.cuda.get_device_name(self.device)})"
        return "CPU"

    @torch.inference_mode()
    def _run(self, tile: np.ndarray) -> np.ndarray:
        t = torch.from_numpy(tile).permute(2, 0, 1).unsqueeze(0).to(self.device)
        t = t.half() if self.half else t.float()
        out = self.model(t / 255.0).clamp_(0, 1)
        return (out.squeeze(0).permute(1, 2, 0).float().cpu().numpy() * 255.0).round().astype(np.uint8)

    def upscale(
        self,
        img: np.ndarray,
        scale: int,
        progress: Optional[Callable[[float], None]] = None,
    ) -> np.ndarray:
        """img — RGB uint8 (H, W, 3). Возвращает RGB uint8 размера (H·scale, W·scale)."""
        h, w = img.shape[:2]
        tile, pad = self.tile, self.spec.tile_pad
        out = np.zeros((h * MODEL_SCALE, w * MODEL_SCALE, 3), dtype=np.uint8)

        tiles_y, tiles_x = math.ceil(h / tile), math.ceil(w / tile)
        total, done = tiles_x * tiles_y, 0
        for ty in range(tiles_y):
            for tx in range(tiles_x):
                # Тайл с перекрытием pad, чтобы на стыках не было швов
                y0, x0 = ty * tile, tx * tile
                y1, x1 = min(y0 + tile, h), min(x0 + tile, w)
                py0, px0 = max(y0 - pad, 0), max(x0 - pad, 0)
                py1, px1 = min(y1 + pad, h), min(x1 + pad, w)

                sr = self._run(np.ascontiguousarray(img[py0:py1, px0:px1]))
                s = MODEL_SCALE
                out[y0 * s:y1 * s, x0 * s:x1 * s] = sr[
                    (y0 - py0) * s:(y1 - py0) * s, (x0 - px0) * s:(x1 - px0) * s
                ]
                done += 1
                if progress:
                    progress(done / total)

        if scale != MODEL_SCALE:
            out = cv2.resize(out, (w * scale, h * scale), interpolation=cv2.INTER_AREA)
        return out
