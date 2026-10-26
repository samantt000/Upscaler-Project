"""Воркер инференса: забирает задачи из очереди Redis и обрабатывает их по одной."""
import json
import logging
import os
import shutil
import threading
import time

import redis

from . import config, journal, moderation
from .pipeline import Models, Rejected, process
from .quality import DriftMonitor

QUEUE_KEY = "queue:jobs"
PROCESSING_KEY = "jobs:processing"
HEARTBEAT_KEY = "worker:heartbeat"
HEARTBEAT_TTL = 30

# Снимает блокировку сессии, только если она принадлежит этой задаче (FR-005)
RELEASE_SESSION = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""

log = logging.getLogger("worker")


def job_dir(job_id: str) -> str:
    return os.path.join(config.DATA_DIR, "jobs", job_id)


class Worker:
    def __init__(self, r: redis.Redis):
        self.r = r
        self.release_session = r.register_script(RELEASE_SESSION)
        self.drift = DriftMonitor()
        self.status = {"ready": False, "model": config.MODEL_NAME}
        self.last_cleanup = 0.0

    # --- служебное ---

    def heartbeat_loop(self) -> None:
        while True:
            try:
                status = {**self.status, "ts": time.time()}
                self.r.set(HEARTBEAT_KEY, json.dumps(status, ensure_ascii=False), ex=HEARTBEAT_TTL)
            except redis.RedisError as e:
                log.warning("heartbeat failed: %s", e)
            time.sleep(10)

    def recover(self) -> None:
        """Задачи, оборванные прошлым запуском воркера, помечаются ошибкой."""
        for job_id in self.r.smembers(PROCESSING_KEY):
            self.finish(job_id, {"status": "error", "error": "Обработка прервана перезапуском сервиса"})
            journal.write("job_error", job_id=job_id, error="worker restart")
        self.r.delete(PROCESSING_KEY)

    def cleanup(self) -> None:
        """FR-010: удаление исходных и обработанных файлов старше срока хранения."""
        now = time.time()
        if now - self.last_cleanup < config.CLEANUP_INTERVAL_SECONDS:
            return
        self.last_cleanup = now
        root = os.path.join(config.DATA_DIR, "jobs")
        if not os.path.isdir(root):
            return
        busy = self.r.smembers(PROCESSING_KEY)
        removed = 0
        for job_id in os.listdir(root):
            path = os.path.join(root, job_id)
            created = self.r.hget(f"job:{job_id}", "created")
            created = float(created) if created else os.path.getmtime(path)
            if job_id not in busy and now - created > config.RETENTION_SECONDS:
                shutil.rmtree(path, ignore_errors=True)
                self.r.delete(f"job:{job_id}")
                removed += 1
        if removed:
            journal.write("cleanup", removed=removed, retention_seconds=config.RETENTION_SECONDS)
            log.info("cleanup: removed %d job(s)", removed)

    def finish(self, job_id: str, fields: dict) -> None:
        key = f"job:{job_id}"
        session = self.r.hget(key, "session")
        if self.r.exists(key):
            self.r.hset(key, mapping={"finished": time.time(), **fields})
        self.r.srem(PROCESSING_KEY, job_id)
        if session:
            self.release_session(keys=[f"session:{session}:active"], args=[job_id])

    # --- обработка ---

    def handle(self, job_id: str, models: Models) -> None:
        key = f"job:{job_id}"
        job = self.r.hgetall(key)
        if not job or job.get("status") != "queued":
            return  # задача удалена пользователем или истёк срок хранения
        self.r.sadd(PROCESSING_KEY, job_id)
        started = time.time()
        self.r.hset(key, mapping={"status": "processing", "started": started, "progress": 0})
        wait_seconds = started - float(job["created"])

        last = [0.0]

        def progress(p: float) -> None:
            if p - last[0] >= 0.02 or p >= 1.0:
                last[0] = p
                self.r.hset(key, "progress", round(p, 3))

        common = {
            "job_id": job_id,
            "session": job.get("session_hash"),
            "format": job["ext"],
            "scale": int(job["scale"]),
            "model": config.MODEL_NAME,
            "device": self.status.get("device"),
        }
        try:
            res = process(job_dir(job_id), job["ext"], int(job["scale"]), models, progress)
        except Rejected as e:
            self.finish(job_id, {"status": "rejected", "error": str(e)})
            journal.write("job_rejected", **common, reason=str(e))
            return
        except Exception as e:  # noqa: BLE001 — любая ошибка задачи не должна ронять воркер
            log.exception("job %s failed", job_id)
            self.finish(job_id, {"status": "error", "error": "Не удалось обработать изображение"})
            journal.write("job_error", **common, error=f"{type(e).__name__}: {e}")
            return

        drift = self.drift.add(res["brisque"])
        self.finish(job_id, {
            "status": "done",
            "progress": 1,
            "result": json.dumps(res, ensure_ascii=False),
        })
        journal.write(
            "job_done", **common,
            wait_seconds=round(wait_seconds, 3),
            moderation="stub" if not moderation.ENABLED else "on",
            **{k: v for k, v in res.items() if k != "warnings"},
            brisque_session=drift,
        )
        if drift.get("drift"):
            journal.write("quality_drift", **drift)
            log.warning("BRISQUE drift: %s", drift)
        log.info("job %s done in %.1fs (%s)", job_id, res["total_seconds"], self.status.get("device"))

    def run(self) -> None:
        threading.Thread(target=self.heartbeat_loop, daemon=True).start()
        t0 = time.perf_counter()
        models = Models.load()
        self.status.update(ready=True, device=models.upscaler.device_name)
        log.info("models loaded in %.1fs, device: %s", time.perf_counter() - t0, self.status["device"])
        journal.write("worker_start", model=config.MODEL_NAME, device=self.status["device"],
                      moderation="stub" if not moderation.ENABLED else "on")
        self.recover()
        while True:
            self.cleanup()
            item = self.r.blpop(QUEUE_KEY, timeout=5)
            if item:
                self.handle(item[1], models)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    r = redis.Redis.from_url(config.REDIS_URL, decode_responses=True)
    for _ in range(30):
        try:
            r.ping()
            break
        except redis.ConnectionError:
            time.sleep(1)
    Worker(r).run()


if __name__ == "__main__":
    main()
