"""ML-тесты ЛР №4 на наборе C (портреты FairFace): FR-006, fairness (AI-01), data drift (AI-03).

Запуск в контейнере воркера; набор C и папка results монтируются, папка tools — тоже,
чтобы не пересобирать образ:

  docker compose -f docker-compose.yml -f docker-compose.gpu.yml run --rm \
      -v D:/datasets/set_c:/eval -v ./results:/results -v ./worker/tools:/app/tools worker \
      python -m tools.lr4_ml fairness --dir /eval/portraits --out /results/fairness.csv

  ... python -m tools.lr4_ml drift --dir /eval/portraits --out /results/drift.csv

Обе команды прогоняют изображения через тот же конвейер, что и сервис (pipeline.process).
"""
import argparse
import csv
import datetime
import io
import os
import random
import shutil
import statistics
import sys
import tempfile
from collections import defaultdict

import numpy as np
from PIL import Image

from app.pipeline import Models, process
from app.quality import DriftMonitor

# Пороги AI-01 и FR-006 (реестр верификации ЛР №4)
MIN_DETECTION = 0.90
MAX_GAP_RACE = 0.10
MAX_GAP_GENDER = 0.05
MIN_REDUCTION = 0.30
MAX_GAP_REDUCTION = 0.05
DRIFT_BATCH = 30


def run_pipeline(models: Models, data: bytes, fmt: str, scale: int = 4) -> dict:
    tmp = tempfile.mkdtemp()
    try:
        with open(os.path.join(tmp, f"input.{fmt}"), "wb") as f:
            f.write(data)
        res = process(tmp, fmt, scale, models, lambda p: None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {k: v for k, v in res.items() if k != "warnings"}


def read_file(path: str) -> tuple[bytes, str]:
    with open(path, "rb") as f:
        data = f.read()
    with Image.open(io.BytesIO(data)) as im:
        fmt = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}[im.format]
    return data, fmt


def write_csv(path: str, rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter=";")
        w.writeheader()
        w.writerows(rows)
    print(f"CSV: {path}")


def verdict(ok: bool) -> str:
    return "Pass" if ok else "Fail"


def record_results(path: str | None, test: str, items: list[tuple[str, bool, str]]) -> None:
    """Дописывает вердикты в общий results/lr4_results.csv (тот же формат, что у pytest).

    items — (тест-кейс, выполнен ли, пояснение); несколько проверок одного тест-кейса
    объединяются: Fail, если не выполнена хотя бы одна.
    """
    if not path:
        return
    merged: dict[str, dict] = {}
    now = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    for tc, ok, detail in items:
        cur = merged.setdefault(tc, {"tc": tc, "status": "Pass", "test": test, "detail": [], "time": now})
        if not ok:
            cur["status"] = "Fail"
        cur["detail"].append(detail)
    for row in merged.values():
        row["detail"] = "; ".join(row["detail"])

    rows: dict[str, dict] = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            rows = {r["tc"]: r for r in csv.DictReader(f, delimiter=";")}
    rows.update(merged)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["tc", "status", "test", "detail", "time"], delimiter=";")
        w.writeheader()
        w.writerows(sorted(rows.values(), key=lambda r: r["tc"]))
    print(f"Вердикты по тест-кейсам дописаны в {path}")


# --- FR-006 и AI-01 ---

def group_stats(rows: list[dict], key: str) -> dict[str, dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r[key]].append(r)
    out = {}
    for g, items in sorted(groups.items()):
        found = [r for r in items if r["faces"] > 0]
        red = [r["face_diff_reduction"] for r in found]
        out[g] = {
            "n": len(items),
            "detection": len(found) / len(items),
            "reduction": statistics.fmean(red) if red else None,
            "flag_rate": (sum(1 for r in found if r["flag_face_zone"]) / len(found)) if found else None,
            "brisque": statistics.fmean(r["brisque"] for r in items),
        }
    return out


def print_groups(title: str, stats: dict[str, dict]) -> None:
    print(f"\n{title}")
    print(f"{'Группа':22} {'n':>4} {'Найдено лиц':>12} {'Снижение':>9} {'Флаг зоны':>10} {'BRISQUE':>8}")
    for g, s in stats.items():
        red = f"{s['reduction']:.1%}" if s["reduction"] is not None else "—"
        flag = f"{s['flag_rate']:.0%}" if s["flag_rate"] is not None else "—"
        print(f"{g:22} {s['n']:>4} {s['detection']:>12.0%} {red:>9} {flag:>10} {s['brisque']:>8.2f}")


def gap(stats: dict[str, dict], field: str) -> float:
    values = [s[field] for s in stats.values() if s[field] is not None]
    return max(values) - min(values) if values else 0.0


def run_fairness(args) -> None:
    labels_path = args.labels or os.path.join(args.dir, "labels.csv")
    with open(labels_path, encoding="utf-8") as f:
        labels = list(csv.DictReader(f))
    if args.limit:
        labels = labels[:args.limit]
    models = Models.load()
    print(f"Портретов: {len(labels)}, устройство: {models.upscaler.device_name}")
    rows = []
    for i, lab in enumerate(labels, 1):
        data, fmt = read_file(os.path.join(args.dir, lab["file"]))
        res = run_pipeline(models, data, fmt, args.scale)
        rows.append({"file": lab["file"], "race": lab["race"], "gender": lab["gender"], "age": lab["age"],
                     "race_gender": f"{lab['race']} / {lab['gender']}", **res})
        if i % 20 == 0:
            print(f"[{i}/{len(labels)}]")
    write_csv(args.out, rows)

    found = [r for r in rows if r["faces"] > 0]
    mean_red = statistics.fmean(r["face_diff_reduction"] for r in found) if found else 0.0
    fr006_ok = bool(found) and mean_red >= MIN_REDUCTION
    fr006_text = (f"лицо найдено на {len(found)} из {len(rows)} портретов; среднее снижение {mean_red:.1%} "
                  f"(порог ≥{MIN_REDUCTION:.0%})")
    print("\n=== TC-FR-006-01: снижение достройки в области лица ===")
    print(f"{fr006_text[0].upper()}{fr006_text[1:]} → {verdict(fr006_ok)}")

    by_race, by_gender = group_stats(rows, "race"), group_stats(rows, "gender")
    print_groups("=== Группы по расе (разметка FairFace) ===", by_race)
    print_groups("=== Группы по полу ===", by_gender)
    print_groups("=== Раса × пол (справочно, n мало) ===", group_stats(rows, "race_gender"))

    det_min = min(s["detection"] for s in [*by_race.values(), *by_gender.values()])
    red_values = [s["reduction"] for s in [*by_race.values(), *by_gender.values()] if s["reduction"] is not None]
    red_min = min(red_values) if red_values else 0.0
    checks = [
        ("TC-AI-01-01", f"доля найденных лиц в каждой группе ≥{MIN_DETECTION:.0%} (минимум {det_min:.0%})",
         det_min >= MIN_DETECTION),
        ("TC-AI-01-01", f"разрыв доли найденных лиц между расами ≤{MAX_GAP_RACE:.2f} ({gap(by_race, 'detection'):.2f})",
         gap(by_race, "detection") <= MAX_GAP_RACE),
        ("TC-AI-01-01", f"разрыв между полами ≤{MAX_GAP_GENDER:.2f} ({gap(by_gender, 'detection'):.2f})",
         gap(by_gender, "detection") <= MAX_GAP_GENDER),
        ("TC-AI-01-02", f"снижение достройки в каждой группе ≥{MIN_REDUCTION:.0%} (минимум {red_min:.1%})",
         red_min >= MIN_REDUCTION),
        ("TC-AI-01-02", f"разрыв снижения между расами ≤{MAX_GAP_REDUCTION:.2f} ({gap(by_race, 'reduction'):.3f})",
         gap(by_race, "reduction") <= MAX_GAP_REDUCTION),
        ("TC-AI-01-02", f"разрыв снижения между полами ≤{MAX_GAP_REDUCTION:.2f} ({gap(by_gender, 'reduction'):.3f})",
         gap(by_gender, "reduction") <= MAX_GAP_REDUCTION),
    ]
    print("\n=== AI-01: fairness ===")
    for tc, text, ok in checks:
        print(f"{tc}: {text} → {verdict(ok)}")
    record_results(args.results, "lr4_ml fairness",
                   [("TC-FR-006-01", fr006_ok, fr006_text), *((tc, ok, text) for tc, text, ok in checks)])
    missed = [r["file"] for r in rows if r["faces"] == 0]
    if missed:
        print(f"\nЛица не найдены ({len(missed)}): {', '.join(missed[:20])}{' …' if len(missed) > 20 else ''}")
    print(f"\nBRISQUE по всем портретам: {statistics.fmean(r['brisque'] for r in rows):.3f}")

    group_rows = [{"разрез": dim, "группа": g, **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in s.items()}}
                  for dim, stats in (("раса", by_race), ("пол", by_gender)) for g, s in stats.items()]
    write_csv(os.path.splitext(args.out)[0] + "_groups.csv", group_rows)


# --- AI-03: дрейф ---

def recompress(data: bytes, quality: int) -> bytes:
    with Image.open(io.BytesIO(data)) as im:
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "JPEG", quality=quality)
        return buf.getvalue()


def add_noise(data: bytes, sigma: float, seed: int) -> bytes:
    with Image.open(io.BytesIO(data)) as im:
        arr = np.asarray(im.convert("RGB")).astype(np.float32)
    arr += np.random.default_rng(seed).normal(0, sigma, arr.shape)
    buf = io.BytesIO()
    Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).save(buf, "PNG")
    return buf.getvalue()


def feed(models, baseline: float, batch: list[tuple[str, bytes, str]], name: str, rows: list) -> dict:
    """Новый монитор (как после перезапуска воркера) получает партию изображений по одному."""
    monitor = DriftMonitor(baseline)
    first_alarm, state = None, {}
    for i, (file, data, fmt) in enumerate(batch, 1):
        res = run_pipeline(models, data, fmt)
        state = monitor.add(res["brisque"])
        if state.get("drift") and first_alarm is None:
            first_alarm = i
        rows.append({"batch": name, "file": file, "brisque": res["brisque"], **state})
    state["first_alarm"] = first_alarm
    return state


def run_drift(args) -> None:
    files = sorted(f for f in os.listdir(args.dir) if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")))
    random.Random(args.seed).shuffle(files)
    control, base = files[:DRIFT_BATCH], files[DRIFT_BATCH:]
    models = Models.load()
    print(f"Базовая часть: {len(base)}, контрольная партия: {len(control)}, устройство: {models.upscaler.device_name}")

    rows = []
    scores = []
    for file in base:
        data, fmt = read_file(os.path.join(args.dir, file))
        res = run_pipeline(models, data, fmt)
        scores.append(res["brisque"])
        rows.append({"batch": "baseline", "file": file, "brisque": res["brisque"]})
    baseline = statistics.fmean(scores)
    print(f"Базовая линия BRISQUE (среднее по {len(base)} портретам): {baseline:.3f}")

    raw = [(f, *read_file(os.path.join(args.dir, f))) for f in control]
    batches = {
        "control": raw,
        "jpeg_q10": [(f, recompress(d, 10), "jpg") for f, d, _ in raw],
        "noise_s25": [(f, add_noise(d, 25, i), "png") for i, (f, d, _) in enumerate(raw)],
    }
    results = {name: feed(models, baseline, batch, name, rows) for name, batch in batches.items()}
    write_csv(args.out, rows)

    print("\n=== AI-03: обнаружение дрейфа (порог отклонения 15%) ===")
    for name, st in results.items():
        alarm = f", первый сигнал на {st['first_alarm']}-м изображении" if st["first_alarm"] else ""
        print(f"{name:10} среднее {st['session_mean']:.3f}, отклонение {st['deviation']:+.1%}, "
              f"дрейф: {'да' if st['drift'] else 'нет'}{alarm}")
    # Ложный сигнал — любой сигнал по ходу контрольной партии, а не только итог в её конце
    ok_control = results["control"]["first_alarm"] is None
    ok_shift = results["jpeg_q10"]["first_alarm"] is not None
    shift_text = (f"сдвиг (JPEG q10) обнаружен на {results['jpeg_q10']['first_alarm']}-м изображении"
                  if ok_shift else "сдвиг (JPEG q10) не обнаружен")
    control_text = ("на контрольной партии ложных сигналов нет" if ok_control else
                    f"ложный сигнал на контрольной партии на {results['control']['first_alarm']}-м изображении")
    print(f"TC-AI-03-01: {shift_text} → {verdict(ok_shift)}")
    print(f"TC-AI-03-02: {control_text} → {verdict(ok_control)}")
    record_results(args.results, "lr4_ml drift", [
        ("TC-AI-03-01", ok_shift, f"{shift_text}; отклонение {results['jpeg_q10']['deviation']:+.1%}"),
        ("TC-AI-03-02", ok_control, f"{control_text}; отклонение {results['control']['deviation']:+.1%}"),
    ])
    print(f"Справочно: шум σ=25 → дрейф {'обнаружен' if results['noise_s25']['drift'] else 'не обнаружен'}")
    print(f"\nДля сервиса: BRISQUE_BASELINE={baseline:.3f} (базовая линия по портретам; см. ограничения набора C)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)
    fair = sub.add_parser("fairness", help="FR-006 и AI-01 на портретах с разметкой")
    fair.add_argument("--dir", required=True)
    fair.add_argument("--labels", default=None, help="по умолчанию <dir>/labels.csv")
    fair.add_argument("--scale", type=int, default=4, choices=(2, 4))
    fair.add_argument("--limit", type=int, default=None)
    fair.add_argument("--out", default="/results/fairness.csv")
    fair.add_argument("--results", default="/results/lr4_results.csv",
                      help="общий файл вердиктов по тест-кейсам; пустая строка — не записывать")
    drift = sub.add_parser("drift", help="AI-03: базовая линия, контрольная и сдвинутые партии")
    drift.add_argument("--dir", required=True)
    drift.add_argument("--seed", type=int, default=42)
    drift.add_argument("--out", default="/results/drift.csv")
    drift.add_argument("--results", default="/results/lr4_results.csv",
                       help="общий файл вердиктов по тест-кейсам; пустая строка — не записывать")
    args = parser.parse_args()
    (run_fairness if args.mode == "fairness" else run_drift)(args)


if __name__ == "__main__":
    sys.exit(main())
