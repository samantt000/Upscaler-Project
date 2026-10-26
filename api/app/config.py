import os

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
DATA_DIR = os.getenv("DATA_DIR", "/data")

# FR-001: ограничения входного файла
MAX_FILE_BYTES = int(os.getenv("MAX_FILE_MB", "10")) * 1024 * 1024
MAX_SIDE = int(os.getenv("MAX_SIDE", "2000"))
MIN_SIDE = 16
ALLOWED_FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}

# FR-002
ALLOWED_SCALES = (2, 4)

# FR-005: не более 1 активной задачи на сессию и не более QUEUE_LIMIT задач в очереди
QUEUE_LIMIT = int(os.getenv("QUEUE_LIMIT", "20"))
SESSION_COOKIE = "sid"
SESSION_LOCK_TTL = 3600

# FR-010: срок хранения (должен совпадать с настройкой воркера)
RETENTION_SECONDS = int(os.getenv("RETENTION_SECONDS", str(24 * 3600)))

# FR-012: базовая линия BRISQUE для страницы метрик
BRISQUE_BASELINE = float(os.getenv("BRISQUE_BASELINE") or 0) or None
