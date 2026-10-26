"""Инференс Real-ESRGAN x4plus с тайловой обработкой (FR-003)."""
import math
from typing import Callable, Optional

import cv2
import numpy as np
import torch

from . import config
from .rrdbnet import RRDBNet

MODEL_SCALE = 4


def resolve_device() -> torch.device:
    if config.DEVICE == "cuda" or (config.DEVICE == "auto" and torch.cuda.is_available()):
        return torch.device("cuda")
    return torch.device("cpu")


class Upscaler:
    def __init__(self, model_path: str = config.MODEL_FILE, device: Optional[torch.device] = None):
        self.device = device or resolve_device()
        if self.device.type == "cpu" and config.TORCH_THREADS > 0:
            torch.set_num_threads(config.TORCH_THREADS)
        self.half = self.device.type == "cuda"

        model = RRDBNet()
        state = torch.load(model_path, map_location="cpu", weights_only=True)
        model.load_state_dict(state.get("params_ema", state.get("params", state)), strict=True)
        model.eval()
        self.model = model.half().to(self.device) if self.half else model.to(self.device)

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
        tile, pad = config.TILE_SIZE, config.TILE_PAD
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
