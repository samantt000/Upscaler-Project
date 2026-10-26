"""Нагрузочный тест (NFR-001, NFR-003): сколько пользователей выдерживает сервис.

Каждый виртуальный пользователь — отдельная анонимная сессия: загружает изображение,
ждёт результата и скачивает его. Время обработки на сервере (по данным API)
публикуется в статистике Locust отдельной строкой «processing».

    pip install -r loadtest/requirements.txt
    locust -f loadtest/locustfile.py --host http://localhost:8000
    # без веб-интерфейса: 20 пользователей одновременно, 10 минут
    locust -f loadtest/locustfile.py --host http://localhost:8000 --headless -u 20 -r 20 -t 10m --csv results/load

Переменные окружения:
    LOAD_IMAGE — путь к тестовому изображению (по умолчанию генерируется 1000×1000 px)
    LOAD_SCALE — коэффициент увеличения (2 или 4, по умолчанию 4)
"""
import io
import os
import time

from locust import HttpUser, between, events, task
from PIL import Image

SCALE = int(os.getenv("LOAD_SCALE", "4"))
POLL_TIMEOUT = 1800


def _test_image() -> tuple[str, bytes]:
    path = os.getenv("LOAD_IMAGE")
    if path:
        with open(path, "rb") as f:
            return os.path.basename(path), f.read()
    buf = io.BytesIO()
    Image.effect_noise((1000, 1000), 48).convert("RGB").save(buf, "JPEG", quality=85)
    return "load.jpg", buf.getvalue()


IMAGE_NAME, IMAGE = _test_image()


def _fire(name: str, started: float, response_time_ms: float, exception=None) -> None:
    events.request.fire(request_type="JOB", name=name, response_time=response_time_ms,
                        response_length=0, exception=exception, context={}, start_time=started)


class ServiceUser(HttpUser):
    wait_time = between(1, 3)

    @task
    def upscale(self):
        started = time.time()
        with self.client.post("/api/jobs", files={"file": (IMAGE_NAME, IMAGE)}, data={"scale": SCALE},
                              name="POST /api/jobs", catch_response=True) as res:
            if res.status_code != 202:
                # 503 — очередь заполнена: это ожидаемая защита (FR-005), а не сбой
                res.failure(f"HTTP {res.status_code}: {res.text[:100]}")
                return
            job_id = res.json()["id"]

        while time.time() - started < POLL_TIMEOUT:
            job = self.client.get(f"/api/jobs/{job_id}", name="GET /api/jobs/[id]").json()
            if job["status"] == "done":
                break
            if job["status"] in ("error", "rejected"):
                _fire("job failed", started, (time.time() - started) * 1000, Exception(job.get("error")))
                return
            time.sleep(1)
        else:
            _fire("job timeout", started, POLL_TIMEOUT * 1000, Exception("timeout"))
            return

        # Полное время с точки зрения пользователя: очередь + обработка
        _fire("end-to-end", started, (time.time() - started) * 1000)
        _fire("processing", started, job["result"]["total_seconds"] * 1000)
        self.client.get(job["files"]["download"], name="GET download")
        self.client.delete(f"/api/jobs/{job_id}", name="DELETE /api/jobs/[id]")
