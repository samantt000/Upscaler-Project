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
import math
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
from app.upscaler import MODELS, Upscaler

EXTENSIONS = ("*.png", "*.jpg", "*.jpeg", "*.webp")
THRESHOLDS = {"psnr": 28.0, "ssim": 0.85, "lpips": 0.25}


def p95(sorted_values: list[float]) -> float:
    """95-й перцентиль методом ближайшего ранга (на малых выборках не занижает значение)."""
    return sorted_values[max(0, math.ceil(0.95 * len(sorted_values)) - 1)]


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
    import torch  # noqa: PLC0415

    names = list(MODELS) if args.models == "all" else [m.strip() for m in args.models.split(",")]
    unknown = [n for n in names if n not in MODELS]
    if unknown:
        sys.exit(f"Неизвестные модели: {', '.join(unknown)}. Доступны: {', '.join(MODELS)}, all")
    upscalers = {n: Upscaler(n) for n in names}
    device = next(iter(upscalers.values())).device
    cuda = device.type == "cuda"
    lp = Lpips(device)
    rng = random.Random(args.seed)
    s, rows = args.scale, []
    files = list_images(args.hr_dir, args.limit)
    print(f"Изображений: {len(files)}, устройство: {next(iter(upscalers.values())).device_name}, "
          f"деградация: {args.degrade}, модели: {', '.join(names)}")

    for i, path in enumerate(files, 1):
        hr, _ = load_rgb(path)
        h, w = (hr.shape[0] // s) * s, (hr.shape[1] // s) * s
        hr = hr[:h, :w]
        lr = np.asarray(Image.fromarray(hr).resize((w // s, h // s), Image.BICUBIC))
        if args.degrade:
            lr = degrade(lr, rng)

        crop = lambda x: x[s:-s, s:-s]  # noqa: E731
        y_hr = crop(to_y(hr))
        row = {"file": os.path.basename(path), "lr_size": f"{w // s}x{h // s}"}
        outputs = {"bicubic": (cv2.resize(lr, (w, h), interpolation=cv2.INTER_CUBIC), None, None)}
        for name, up in upscalers.items():
            if cuda:
                torch.cuda.reset_peak_memory_stats()
            t0 = time.perf_counter()
            sr = up.upscale(lr, s)
            if cuda:
                torch.cuda.synchronize()
            vram = torch.cuda.max_memory_allocated() / 2**20 if cuda else None
            outputs[name] = (sr, time.perf_counter() - t0, vram)

        for name, (img, seconds, vram) in outputs.items():
            y = crop(to_y(img))
            row[f"psnr_{name}"] = round(psnr(y, y_hr), 4)
            row[f"ssim_{name}"] = round(ssim(y, y_hr), 4)
            value = lp(crop(img), crop(hr))
            row[f"lpips_{name}"] = round(value, 4) if value is not None else ""
            if seconds is not None:
                row[f"sec_{name}"] = round(seconds, 3)
                row[f"vram_mb_{name}"] = round(vram) if vram is not None else ""
        rows.append(row)
        print(f"[{i}/{len(files)}] {row['file']}: " + "; ".join(
            f"{n} {row[f'psnr_{n}']:.2f} дБ / {row[f'lpips_{n}'] or '—'}" for n in outputs))

    write_csv(args.out, rows)
    print_reference_summary(rows, ["bicubic", *names], {n: up.title for n, up in upscalers.items()},
                            degraded=args.degrade)


def print_reference_summary(rows: list[dict], names: list[str], titles: dict[str, str],
                            degraded: bool = False) -> None:
    mean = lambda k: statistics.fmean(r[k] for r in rows if r.get(k) not in ("", None))  # noqa: E731
    cell = lambda v, width, fmt: (format(v, fmt) if v is not None else "—").rjust(width)  # noqa: E731
    has_lpips = rows[0].get("lpips_bicubic") != ""
    print("\n=== Итог (среднее по изображениям) ===")
    print(f"{'Модель':32} {'PSNR, дБ':>9} {'SSIM':>7} {'LPIPS':>7} {'Время, с':>9} {'p95, с':>7} {'VRAM, МБ':>9}")
    for n in names:
        times = sorted(r[f"sec_{n}"] for r in rows) if f"sec_{n}" in rows[0] else []
        vram = max((r[f"vram_mb_{n}"] for r in rows if r.get(f"vram_mb_{n}") not in ("", None)), default=None)
        print(f"{titles.get(n, 'Бикубическая интерполяция'):32}"
              f" {cell(mean(f'psnr_{n}'), 9, '.2f')} {cell(mean(f'ssim_{n}'), 7, '.4f')}"
              f" {cell(mean(f'lpips_{n}') if has_lpips else None, 7, '.4f')}"
              f" {cell(statistics.fmean(times) if times else None, 9, '.2f')}"
              f" {cell(p95(times) if times else None, 7, '.2f')}"
              f" {cell(vram, 9, '.0f')}")

    # ЛР №2, раздел 4.1: пороги NFR-004–006 проверяются на наборе A; для набора B порогов нет,
    # критерий — модель превосходит бикубическую интерполяцию по LPIPS
    if degraded:
        print("\nНабор B: абсолютные пороги не применяются. Критерий: LPIPS лучше бикубической интерполяции.")
    else:
        print("\nНабор A: пороги NFR-004–006 (да/нет) и сравнение с бикубической интерполяцией:")
    n_rows = len(rows)
    for n in names[1:]:
        parts = []
        if not degraded:
            parts += [f"PSNR {'да' if mean(f'psnr_{n}') >= THRESHOLDS['psnr'] else 'нет'}",
                      f"SSIM {'да' if mean(f'ssim_{n}') >= THRESHOLDS['ssim'] else 'нет'}"]
            if has_lpips:
                parts.append(f"LPIPS {'да' if mean(f'lpips_{n}') <= THRESHOLDS['lpips'] else 'нет'}")
        if has_lpips:
            wins = sum(1 for r in rows if r[f"lpips_{n}"] < r["lpips_bicubic"])
            gain = 1 - mean(f"lpips_{n}") / mean("lpips_bicubic")
            parts.append(f"LPIPS лучше на {gain:.0%} (на {wins} из {n_rows} изображений)")
            if degraded:
                parts.append("критерий " + ("выполнен" if gain > 0 else "НЕ выполнен"))
        ssim_wins = sum(1 for r in rows if r[f"ssim_{n}"] > r["ssim_bicubic"])
        parts.append(f"PSNR {mean(f'psnr_{n}') - mean('psnr_bicubic'):+.2f} дБ")
        parts.append(f"SSIM {mean(f'ssim_{n}') - mean('ssim_bicubic'):+.4f} (лучше на {ssim_wins} из {n_rows})")
        print(f"{titles[n]:32} " + "; ".join(parts))


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
          f"p95 {p95(times):.2f} с")


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
    ref.add_argument("--models", default="realesrgan",
                     help=f"модели через запятую или all; доступны: {', '.join(MODELS)}")

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
