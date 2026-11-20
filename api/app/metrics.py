"""Сводные метрики по журналу (FR-011, FR-012, NFR-011)."""
from collections import Counter

from . import config, journal


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 3)


def resolution_bucket(w: int, h: int) -> str:
    side = max(w, h)
    for limit in (256, 512, 1024, 1500):
        if side <= limit:
            return f"≤{limit}"
    return f"≤{config.MAX_SIDE}"


def summary(queue_length: int, processing: int, worker: dict | None) -> dict:
    records = journal.read()
    done = [r for r in records if r["event"] == "job_done"]
    errors = [r for r in records if r["event"] == "job_error"]
    rejected_upload = [r for r in records if r["event"] == "upload_rejected"]
    downloaded = {r["job_id"] for r in records if r["event"] == "downloaded"}

    by_device: dict[str, list[float]] = {}
    for r in done:
        by_device.setdefault(r.get("device") or "?", []).append(r["total_seconds"])

    finished = len(done) + len(errors)
    brisque = [r["brisque"] for r in done if r.get("brisque") is not None]
    brisque_mean = round(sum(brisque) / len(brisque), 3) if brisque else None
    brisque_in = [r["brisque_input"] for r in done if r.get("brisque_input") is not None]
    brisque_in_mean = round(sum(brisque_in) / len(brisque_in), 3) if brisque_in else None

    def deviation_of(mean, baseline, count):
        if not (baseline and mean is not None):
            return None
        deviation = (mean - baseline) / baseline
        return {"baseline": baseline, "deviation": round(deviation, 4),
                "drift": count >= config.BRISQUE_DRIFT_MIN_JOBS and abs(deviation) > 0.15}

    drift = deviation_of(brisque_mean, config.BRISQUE_BASELINE, len(brisque))
    input_drift = deviation_of(brisque_in_mean, config.BRISQUE_INPUT_BASELINE, len(brisque_in))

    face_jobs = [r for r in done if r.get("faces")]
    reductions = [r["face_diff_reduction"] for r in face_jobs if "face_diff_reduction" in r]

    return {
        "worker": worker,
        "queue": {"length": queue_length, "processing": processing, "limit": config.QUEUE_LIMIT},
        "jobs": {
            "done": len(done),
            "errors": len(errors),
            "error_rate": round(len(errors) / finished, 4) if finished else None,
            "upload_rejected": len(rejected_upload),
            "downloaded": len(downloaded & {r["job_id"] for r in done}),
            "download_rate": round(len(downloaded & {r["job_id"] for r in done}) / len(done), 4) if done else None,
        },
        "processing_time": {
            device: {"count": len(v), "p50": percentile(v, 0.5), "p95": percentile(v, 0.95),
                     "max": round(max(v), 3)}
            for device, v in by_device.items()
        },
        "wait_time_p95": percentile([r.get("wait_seconds", 0) for r in done], 0.95),
        "inputs": {
            "formats": dict(Counter(r["format"] for r in done)),
            "scales": dict(Counter(f"×{r['scale']}" for r in done)),
            "resolutions": dict(Counter(resolution_bucket(r["width"], r["height"]) for r in done)),
        },
        "faces": {
            "jobs_with_faces": len(face_jobs),
            "flagged_zone": sum(1 for r in face_jobs if r.get("flag_face_zone")),
            "mean_diff_reduction": round(sum(reductions) / len(reductions), 4) if reductions else None,
        },
        "quality": {"brisque_mean": brisque_mean, "brisque_count": len(brisque), "drift": drift,
                    "input_mean": brisque_in_mean, "input_drift": input_drift},
        "models": dict(Counter(r.get("model") for r in done)),
    }
