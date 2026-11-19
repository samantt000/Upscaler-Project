"""Сборка набора C (ЛР №4): портреты FairFace, карточки товаров и прочие фото.

Набор C — реальные изображения для FR-006 (лица), FR-012 (базовая линия BRISQUE),
fairness- и drift-тестов. Структура результата:

    datasets/set_c/
      portraits/            FairFace, 224×224 px, вход сервиса как есть
        labels.csv          file, race, gender, age — разметка для fairness
      products/lr/          вход сервиса: фото уменьшено в 4 раза и сжато JPEG
      products/hr/          эталон: то же фото до уменьшения (для PSNR/SSIM/LPIPS)
      other/lr/, other/hr/  то же для прочих фото

Запуск на ПК (не в контейнере), Python 3.10+:

    pip install pillow datasets
    # 1. Портреты: 7 групп × 2 пола × 10 = 140 изображений, качается только нужное (~50 МБ)
    python tools/build_set_c.py fairface --out datasets/set_c/portraits --per-cell 10
    # 2. Свои фото: исходники с телефона кладутся в datasets/raw/products и datasets/raw/other
    python tools/build_set_c.py photos --src datasets/raw/products --out datasets/set_c/products
    python tools/build_set_c.py photos --src datasets/raw/other --out datasets/set_c/other

FairFace: Kärkkäinen K., Joo J. FairFace: Face Attribute Dataset for Balanced Race, Gender,
and Age. WACV 2021. Лицензия CC BY 4.0, https://github.com/joojs/fairface
"""
import argparse
import csv
import io
import os
import random
import sys
from collections import defaultdict

from PIL import Image, ImageOps

PHOTO_EXT = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif")


def build_fairface(args) -> None:
    try:
        from datasets import load_dataset  # noqa: PLC0415
    except ImportError:
        sys.exit("Нужен пакет datasets: pip install datasets")

    # Потоковое чтение: валидационная часть целиком не скачивается, берутся первые подходящие
    ds = load_dataset("HuggingFaceM4/FairFace", args.margin, split="validation", streaming=True)
    names = {k: ds.features[k].names for k in ("race", "gender", "age")}
    races, genders = names["race"], names["gender"]
    need = {(r, g): args.per_cell for r in races for g in genders}
    counts: dict[tuple[str, str], int] = defaultdict(int)
    os.makedirs(args.out, exist_ok=True)
    rows = []

    for i, item in enumerate(ds):
        race, gender = races[item["race"]], genders[item["gender"]]
        age = names["age"][item["age"]]
        if age in args.skip_age or counts[(race, gender)] >= need[(race, gender)]:
            continue
        counts[(race, gender)] += 1
        name = f"{race.replace(' ', '_')}_{gender}_{counts[(race, gender)]:02d}.png"
        item["image"].convert("RGB").save(os.path.join(args.out, name))
        rows.append({"file": name, "race": race, "gender": gender, "age": age, "source_index": i})
        done = sum(counts.values())
        if done % 20 == 0:
            print(f"сохранено {done}/{sum(need.values())}")
        if all(counts[k] >= v for k, v in need.items()):
            break

    with open(os.path.join(args.out, "labels.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["file", "race", "gender", "age", "source_index"])
        writer.writeheader()
        writer.writerows(rows)

    short = [f"{r}/{g}: {counts[(r, g)]}" for r in races for g in genders if counts[(r, g)] < args.per_cell]
    print(f"Готово: {len(rows)} портретов в {args.out}")
    if short:
        print("Не набрано до нормы:", ", ".join(short))


def open_photo(path: str) -> Image.Image:
    if path.lower().endswith((".heic", ".heif")):
        try:
            import pillow_heif  # noqa: PLC0415

            pillow_heif.register_heif_opener()
        except ImportError:
            raise RuntimeError("HEIC: pip install pillow-heif или снимайте в JPEG")
    im = Image.open(path)
    return ImageOps.exif_transpose(im).convert("RGB")


def build_photos(args) -> None:
    hr_dir, lr_dir = os.path.join(args.out, "hr"), os.path.join(args.out, "lr")
    os.makedirs(hr_dir, exist_ok=True)
    os.makedirs(lr_dir, exist_ok=True)
    rng = random.Random(args.seed)
    target = args.lr_side * 4
    prefix = os.path.basename(os.path.normpath(args.out))  # products_001, other_001
    files = sorted(f for f in os.listdir(args.src) if f.lower().endswith(PHOTO_EXT))
    if not files:
        sys.exit(f"В {args.src} нет фото ({', '.join(PHOTO_EXT)})")

    rows = []
    for n, fname in enumerate(files, 1):
        try:
            im = open_photo(os.path.join(args.src, fname))
        except Exception as e:  # noqa: BLE001
            print(f"пропущен {fname}: {e}")
            continue
        w, h = im.size
        if max(w, h) < target:
            print(f"пропущен {fname}: {w}×{h} меньше эталона {target} px по длинной стороне")
            continue
        # Эталон: длинная сторона = 4 × lr_side, стороны кратны 4 (уменьшение снимает шум матрицы)
        k = target / max(w, h)
        hw, hh = round(w * k) // 4 * 4, round(h * k) // 4 * 4
        hr = im.resize((hw, hh), Image.LANCZOS)
        # Вход: бикубическое уменьшение ×4 и JPEG, как после пересылки через мессенджер
        lr = hr.resize((hw // 4, hh // 4), Image.BICUBIC)
        quality = rng.randint(args.jpeg_min, args.jpeg_max)
        stem = f"{prefix}_{n:03d}"
        hr.save(os.path.join(hr_dir, f"{stem}.png"))
        buf = io.BytesIO()
        lr.save(buf, "JPEG", quality=quality)
        with open(os.path.join(lr_dir, f"{stem}.jpg"), "wb") as f:
            f.write(buf.getvalue())
        rows.append({"file": stem, "source": fname, "hr": f"{hw}x{hh}", "lr": f"{hw // 4}x{hh // 4}",
                     "jpeg_quality": quality})
        print(f"[{n}/{len(files)}] {fname} → {stem}: эталон {hw}×{hh}, вход {hw // 4}×{hh // 4}, JPEG {quality}")

    with open(os.path.join(args.out, "index.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["file", "source", "hr", "lr", "jpeg_quality"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Готово: {len(rows)} фото в {args.out} (исходники не изменены)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)

    ff = sub.add_parser("fairface", help="стратифицированная выборка портретов FairFace")
    ff.add_argument("--out", required=True)
    ff.add_argument("--per-cell", type=int, default=10, help="изображений на группу × пол")
    ff.add_argument("--margin", default="1.25", choices=("0.25", "1.25"),
                    help="отступ вокруг лица: 1.25 — больше фона, ближе к обычному портрету")
    ff.add_argument("--skip-age", nargs="*", default=["0-2"],
                    help="возрастные группы, которые не брать (по умолчанию младенцы)")

    ph = sub.add_parser("photos", help="свои фото: эталон + уменьшенный сжатый вход")
    ph.add_argument("--src", required=True)
    ph.add_argument("--out", required=True)
    ph.add_argument("--lr-side", type=int, default=480, help="длинная сторона входа сервиса, px")
    ph.add_argument("--jpeg-min", type=int, default=70)
    ph.add_argument("--jpeg-max", type=int, default=90)
    ph.add_argument("--seed", type=int, default=42)

    args = parser.parse_args()
    (build_fairface if args.mode == "fairface" else build_photos)(args)


if __name__ == "__main__":
    main()
