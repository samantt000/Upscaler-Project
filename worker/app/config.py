import os


def _float_or_none(name: str):
    value = os.getenv(name, "").strip()
    return float(value) if value else None


REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
DATA_DIR = os.getenv("DATA_DIR", "/data")
MODELS_DIR = os.getenv("MODELS_DIR", "/models")

# auto — GPU при наличии, иначе CPU; можно принудительно задать cpu или cuda
DEVICE = os.getenv("DEVICE", "auto")
# Размер тайла (px входа). Меньше — меньше памяти, но больше накладных расходов
TILE_SIZE = int(os.getenv("TILE_SIZE", "256"))
TILE_PAD = 10
# Число потоков PyTorch на CPU (0 — по умолчанию PyTorch)
TORCH_THREADS = int(os.getenv("TORCH_THREADS", "0"))

MODEL_NAME = "Real-ESRGAN x4plus"
MODEL_FILE = os.path.join(MODELS_DIR, "RealESRGAN_x4plus.pth")
FACE_MODEL_FILE = os.path.join(MODELS_DIR, "face_detection_yunet_2023mar.onnx")
BRISQUE_MODEL_FILE = os.path.join(MODELS_DIR, "brisque_model_live.yml")
BRISQUE_RANGE_FILE = os.path.join(MODELS_DIR, "brisque_range_live.yml")

# FR-006: доля, на которую снижается достройка деталей в области лица (0.5 = на 50%)
FACE_DETAIL_REDUCTION = float(os.getenv("FACE_DETAIL_REDUCTION", "0.5"))
FACE_SCORE_THRESHOLD = float(os.getenv("FACE_SCORE_THRESHOLD", "0.8"))

# Карта изменений: пиксель считается «достроенным», если |SR − bicubic| > порога (0–255)
DIFF_THRESHOLD = int(os.getenv("DIFF_THRESHOLD", "12"))
# Индекс достройки в области лица, выше которого выставляется флаг «зона активной генерации»
FACE_ZONE_INDEX_THRESHOLD = float(os.getenv("FACE_ZONE_INDEX_THRESHOLD", "0.15"))

# FR-012: базовая линия BRISQUE (среднее по набору C); пусто — сравнение не выполняется
BRISQUE_BASELINE = _float_or_none("BRISQUE_BASELINE")
BRISQUE_DRIFT_THRESHOLD = 0.15

# FR-010: срок хранения файлов и период проверки
RETENTION_SECONDS = int(os.getenv("RETENTION_SECONDS", str(24 * 3600)))
CLEANUP_INTERVAL_SECONDS = int(os.getenv("CLEANUP_INTERVAL_SECONDS", "600"))

PREVIEW_MAX_SIDE = 1600
