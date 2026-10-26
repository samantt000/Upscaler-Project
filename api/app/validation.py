"""Проверка входного файла (FR-001)."""
import io
import warnings

from PIL import Image

from . import config


class InvalidImage(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def validate(data: bytes) -> tuple[str, int, int]:
    """Возвращает (расширение, ширина, высота) или бросает InvalidImage."""
    if not data:
        raise InvalidImage("Файл пустой")
    if len(data) > config.MAX_FILE_BYTES:
        raise InvalidImage(
            f"Файл больше {config.MAX_FILE_BYTES // (1024 * 1024)} МБ", status=413
        )

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            # Image.open читает только заголовок: формат и размеры известны без декодирования
            with Image.open(io.BytesIO(data)) as im:
                fmt, (w, h) = im.format, im.size
    except Image.DecompressionBombError:
        raise InvalidImage(f"Разрешение больше {config.MAX_SIDE}×{config.MAX_SIDE} px")
    except Exception:
        raise InvalidImage("Файл повреждён или не является изображением")

    if fmt not in config.ALLOWED_FORMATS:
        raise InvalidImage("Неподдерживаемый формат. Допустимы JPG, PNG и WEBP", status=415)
    if w > config.MAX_SIDE or h > config.MAX_SIDE:
        raise InvalidImage(
            f"Разрешение {w}×{h} px больше допустимого {config.MAX_SIDE}×{config.MAX_SIDE} px"
        )
    if w < config.MIN_SIDE or h < config.MIN_SIDE:
        raise InvalidImage(f"Изображение меньше {config.MIN_SIDE}×{config.MIN_SIDE} px")

    # Полное декодирование ловит обрезанные и повреждённые файлы
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
    except Exception:
        raise InvalidImage("Файл повреждён или не является изображением")
    return config.ALLOWED_FORMATS[fmt], w, h
