"""Оценка качества и времени обработки на тестовых наборах (NFR-001, NFR-004–006, FR-006, FR-012).

Наборы из ЛР №2:
  A — HR-изображения (DIV2K-val), бикубическое уменьшение ×4 → PSNR/SSIM/LPIPS с эталоном;
  B — те же изображения + JPEG-сжатие (качество 30–70) и гауссов шум (σ 5–25);
  C — реальные изображения без эталона → BRISQUE (базовая линия FR-012), лица (FR-006), время.

Запуск в контейнере воркера (папка с изображениями монтируется в /eval):
  docker compose -f docker-compose.yml -f docker-compose.gpu.yml run --rm \
      -v D:/datasets/DIV2K_valid_HR:/eval/hr -v ./results:/results worker \
      python -m tools.eval_metrics ref --hr-dir /eval/hr --limit 100 --out /results/set_a.csv
"""
import argparse
import csv
import glob
import os
import random
import shutil
import statistics
import sys
import tempfile
import time

import cv2
import numpy as np
from PIL import Image

from app.pipeline import Models, load_rgb, process

EXTENSIONS = ("*.png", "*.jpg", "*.jpeg", "*.webp")
THRESHOLDS = {"psnr": 28.0, "ssim": 0.85, "lpips": 0.25}


def list_images(folder: str, limit: int | None) -> list[str]:
    files = sorted(f for ext in EXTENSIONS for f in glob.glob(os.path.join(folder, ext)))
    return files[:limit] if limit else files


# --- метрики с эталоном (как в публикациях по SR: канал Y, обрезка рамки шириной scale) ---

def to_y(img_rgb: np.ndarray) -> np.ndarray:
    img = img_rgb.astype(np.float64) / 255.0
    return 16.0 + 65.481 * img[..., 0] + 128.553 * img[..., 1] + 24.966 * img[..., 2]


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = np.mean((a - b) ** 2)
    return float("inf") if mse == 0 else 10 * np.log10(255.0 ** 2 / mse)


def ssim(a: np.ndarray, b: np.ndarray) -> float:
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    blur = lambda x: cv2.GaussianBlur(x, (11, 11), 1.5)[5:-5, 5:-5]  # noqa: E731
    mu_a, mu_b = blur(a), blur(b)
    var_a = blur(a * a) - mu_a ** 2
    var_b = blur(b * b) - mu_b ** 2
    cov = blur(a * b) - mu_a * mu_b
    s = ((2 * mu_a * mu_b + c1) * (2 * cov + c2)) / ((mu_a ** 2 + mu_b ** 2 + c1) * (var_a + var_b + c2))
    return float(s.mean())


class Lpips:
    def __init__(self, device):
        try:
            import lpips  # noqa: PLC0415 — необязательная зависимость (WITH_EVAL=true)
            import torch  # noqa: PLC0415
        except ImportError:
            self.model = None
            print("LPIPS пропущен: в образе нет пакета lpips (соберите воркер с WITH_EVAL=true)")
            return
        self.torch = torch
        self.device = device
        self.model = lpips.LPIPS(net="alex", verbose=False).to(device).eval()

    def __call__(self, a: np.ndarray, b: np.ndarray) -> float | None:
        if self.model is None:
            return None
        t = lambda x: (self.torch.from_numpy(x).permute(2, 0, 1)[None].float() / 127.5 - 1).to(self.device)  # noqa: E731
        with self.torch.inference_mode():
            return float(self.model(t(a), t(b)).item())


def degrade(lr: np.ndarray, rng: random.Random) -> np.ndarray:
    """Набор B: JPEG-сжатие и гауссов шум со случайными параметрами (воспроизводимо по seed)."""
    noisy = lr.astype(np.float32) + np.random.default_rng(rng.randrange(1 << 30)).normal(
        0, rng.uniform(5, 25), lr.shape)
    noisy = np.clip(noisy, 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(noisy, cv2.COLOR_RGB2BGR),
                           [cv2.IMWRITE_JPEG_QUALITY, rng.randint(30, 70)])
    return cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)


def run_reference(args) -> None:
    models = Models.load()
    upscaler = models.upscaler
    lp = Lpips(upscaler.device)
    rng = random.Random(args.seed)
    s, rows = args.scale, []
    files = list_images(args.hr_dir, args.limit)
    print(f"Изображений: {len(files)}, устройство: {upscaler.device_name}, деградация: {args.degrade}")

    for i, path in enumerate(files, 1):
        hr, _ = load_rgb(path)
        h, w = (hr.shape[0] // s) * s, (hr.shape[1] // s) * s
        hr = hr[:h, :w]
        lr = np.asarray(Image.fromarray(hr).resize((w // s, h // s), Image.BICUBIC))
        if args.degrade:
            lr = degrade(lr, rng)

        t0 = time.perf_counter()
        sr = upscaler.upscale(lr, s)
        if upscaler.device.type == "cuda":
            import torch  # noqa: PLC0415
            torch.cuda.synchronize()
        seconds = time.perf_counter() - t0
        bicubic = cv2.resize(lr, (w, h), interpolation=cv2.INTER_CUBIC)

        crop = lambda x: x[s:-s, s:-s]  # noqa: E731
        y_hr = crop(to_y(hr))
        row = {"file": os.path.basename(path), "lr_size": f"{w // s}x{h // s}", "seconds": round(seconds, 3)}
        for name, img in (("model", sr), ("bicubic", bicubic)):
            y = crop(to_y(img))
            row[f"psnr_{name}"] = round(psnr(y, y_hr), 4)
            row[f"ssim_{name}"] = round(ssim(y, y_hr), 4)
            value = lp(crop(img), crop(hr))
            row[f"lpips_{name}"] = round(value, 4) if value is not None else ""
        rows.append(row)
        print(f"[{i}/{len(files)}] {row['file']}: PSNR {row['psnr_model']:.2f} (бикуб. {row['psnr_bicubic']:.2f}), "
              f"SSIM {row['ssim_model']:.3f}, LPIPS {row['lpips_model'] or '—'}, {seconds:.2f} с")

    write_csv(args.out, rows)
    mean = lambda k: statistics.fmean(r[k] for r in rows if r[k] != "")  # noqa: E731
    print("\n=== Итог ===")
    for metric in ("psnr", "ssim", "lpips"):
        if rows[0][f"{metric}_model"] == "":
            continue
        m, b = mean(f"{metric}_model"), mean(f"{metric}_bicubic")
        ok = m >= THRESHOLDS[metric] if metric != "lpips" else m <= THRESHOLDS[metric]
        sign = "≥" if metric != "lpips" else "≤"
        print(f"{metric.upper():6} модель {m:.4f} | бикубическая {b:.4f} | порог {sign}{THRESHOLDS[metric]} — "
              f"{'выполнен' if ok else 'НЕ выполнен'}")
    gain = mean("psnr_model") - mean("psnr_bicubic")
    print(f"Выигрыш PSNR над бикубической: {gain:+.2f} дБ "
          f"({'применение ИИ оправдано' if gain >= 1 else 'меньше 1 дБ — см. критерий отказа от ИИ'})")
    times = sorted(r["seconds"] for r in rows)
    print(f"Время модели: среднее {statistics.fmean(times):.2f} с, p95 {times[int(0.95 * (len(times) - 1))]:.2f} с")


def run_no_reference(args) -> None:
    """Набор C: полный конвейер сервиса (как для пользователя), без эталона."""
    models = Models.load()
    rows = []
    files = list_images(args.dir, args.limit)
    print(f"Изображений: {len(files)}, устройство: {models.upscaler.device_name}")
    for i, path in enumerate(files, 1):
        with Image.open(path) as im:
            fmt = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}.get(im.format, "png")
        tmp = tempfile.mkdtemp()
        try:
            shutil.copy(path, os.path.join(tmp, f"input.{fmt}"))
            res = process(tmp, fmt, args.scale, models, lambda p: None)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        row = {"file": os.path.basename(path), **{k: v for k, v in res.items() if k != "warnings"}}
        rows.append(row)
        print(f"[{i}/{len(files)}] {row['file']}: BRISQUE {res['brisque']:.2f}, лиц {res['faces']}, "
              f"снижение в зоне лица {res.get('face_diff_reduction', '—')}, {res['total_seconds']:.2f} с")

    write_csv(args.out, rows)
    brisque = [r["brisque"] for r in rows]
    print("\n=== Итог ===")
    print(f"BRISQUE среднее: {statistics.fmean(brisque):.3f} — укажите в .env как BRISQUE_BASELINE")
    faces = [r["face_diff_reduction"] for r in rows if r.get("faces")]
    if faces:
        print(f"FR-006: среднее снижение достройки в области лиц {statistics.fmean(faces):.1%} "
              f"(порог ≥30%, изображений с лицами: {len(faces)})")
    times = sorted(r["total_seconds"] for r in rows)
    print(f"Полное время обработки: среднее {statistics.fmean(times):.2f} с, "
          f"p95 {times[int(0.95 * (len(times) - 1))]:.2f} с")


def write_csv(path: str | None, rows: list[dict]) -> None:
    if not path or not rows:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        # У изображений с лицами больше полей, поэтому заголовок — объединение ключей всех строк
        fields = list(dict.fromkeys(k for row in rows for k in row))
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Результаты по изображениям: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)

    ref = sub.add_parser("ref", help="наборы A и B: метрики с эталоном")
    ref.add_argument("--hr-dir", required=True)
    ref.add_argument("--degrade", action="store_true", help="набор B: JPEG + шум")
    ref.add_argument("--seed", type=int, default=42)

    noref = sub.add_parser("noref", help="набор C: BRISQUE, лица, время")
    noref.add_argument("--dir", required=True)

    for p in (ref, noref):
        p.add_argument("--scale", type=int, default=4, choices=(2, 4))
        p.add_argument("--limit", type=int, default=None)
        p.add_argument("--out", default=None, help="CSV с результатами по каждому изображению")

    args = parser.parse_args()
    (run_reference if args.mode == "ref" else run_no_reference)(args)


if __name__ == "__main__":
    sys.exit(main())
