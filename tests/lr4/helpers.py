"""Общие функции системных тестов ЛР №4: клиент API, генераторы файлов, доступ к Docker."""
import io
import json
import os
import shlex
import struct
import subprocess
import time
import zlib

import requests
from PIL import Image

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FINAL = ("done", "error", "rejected")


# --- клиент API ---

class Api:
    def __init__(self, url: str, timeout: float, key: str | None = None):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.key = key

    def session(self) -> requests.Session:
        """Новая анонимная сессия: cookie sid выдаст сервер при первом запросе.

        В режиме демонстрации (задан ACCESS_KEY) сессия сначала открывает ссылку с ключом,
        как жюри по QR-коду, иначе все запросы получат 401/403.
        """
        s = requests.Session()
        s.trust_env = False  # прокси из окружения не должен перехватывать localhost
        if self.key:
            s.get(f"{self.url}/", params={"key": self.key}, timeout=30)
        return s

    def get(self, s: requests.Session, path: str, **kw) -> requests.Response:
        return s.get(f"{self.url}{path}", timeout=kw.pop("timeout", 30), **kw)

    def upload(self, s: requests.Session, data: bytes, name: str, scale=2,
               content_type: str | None = None) -> requests.Response:
        file = (name, data, content_type) if content_type else (name, data)
        return s.post(f"{self.url}/api/jobs", files={"file": file}, data={"scale": scale}, timeout=120)

    def status(self, s: requests.Session, job_id: str) -> dict:
        return self.get(s, f"/api/jobs/{job_id}").json()

    def wait(self, s: requests.Session, job_id: str, timeout: float | None = None) -> dict:
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            res = self.get(s, f"/api/jobs/{job_id}")
            if res.status_code == 404:
                return {"status": "gone"}
            job = res.json()
            if job["status"] in FINAL:
                return job
            time.sleep(0.5)
        raise TimeoutError(f"задача {job_id} не завершилась за {timeout or self.timeout} с")

    def delete(self, s: requests.Session, job_id: str) -> None:
        """Удаляет задачу; если она обрабатывается, ждёт завершения."""
        for _ in range(2):
            res = s.delete(f"{self.url}/api/jobs/{job_id}", timeout=30)
            if res.status_code != 409:
                return
            self.wait(s, job_id)

    def health(self) -> requests.Response:
        return self.get(self.session(), "/api/health")

    def metrics(self) -> dict:
        return self.get(self.session(), "/api/metrics").json()

    def wait_ready(self, timeout: float = 300) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                code = self.health().status_code
                if code == 200:
                    return True
                if code in (401, 403):  # режим демонстрации без верного ключа: ждать бесполезно
                    return False
            except requests.RequestException:
                pass
            time.sleep(2)
        return False


# --- генераторы изображений ---

def encode(img: Image.Image, fmt: str, **params) -> bytes:
    buf = io.BytesIO()
    img.save(buf, fmt, **params)
    return buf.getvalue()


def noise(size=(320, 240), fmt="JPEG", **params) -> bytes:
    """Шумовое изображение: плохо сжимается, обработка заметно отличается от бикубической."""
    return encode(Image.effect_noise(size, 64).convert("RGB"), fmt, **params)


def smooth(size, fmt="JPEG", **params) -> bytes:
    """Крупная, но хорошо сжимаемая картинка: десятки килобайт при любом разрешении."""
    return encode(Image.radial_gradient("L").resize(size).convert("RGB"), fmt, **({"quality": 85} | params))


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def png_exact_size(total: int, size=(1400, 1400)) -> bytes:
    """Корректный PNG ровно заданного размера в байтах (добивка частным вспомогательным чанком)."""
    base = encode(Image.effect_noise(size, 64).convert("RGB"), "PNG", compress_level=0)
    pad = total - len(base) - 12
    if pad < 0:
        raise ValueError(f"базовый PNG {len(base)} байт больше цели {total}")
    iend = base.rindex(b"IEND") - 4
    # «ppAd»: вспомогательный (p), частный (p), зарезервированный бит (A), можно копировать (d)
    return base[:iend] + _chunk(b"ppAd", b"\0" * pad) + base[iend:]


def png_bomb(side=50000) -> bytes:
    """PNG 1 бит/пиксель side×side: ~300 КБ на диске, сотни мегабайт после декодирования."""
    raw_row = b"\0" + b"\0" * ((side + 7) // 8)
    comp = zlib.compressobj(9)
    idat = b"".join(comp.compress(raw_row) for _ in range(side)) + comp.flush()
    ihdr = struct.pack(">IIBBBBB", side, side, 1, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


def jpeg_with_orientation(size=(320, 240), orientation=6) -> bytes:
    img = Image.effect_noise(size, 64).convert("RGB")
    exif = Image.Exif()
    exif[0x0112] = orientation
    return encode(img, "JPEG", quality=90, exif=exif.tobytes())


def animated(fmt: str, size=(200, 150), frames=3) -> bytes:
    imgs = [Image.effect_noise(size, 30 + 20 * i).convert("RGB") for i in range(frames)]
    params = {"save_all": True, "append_images": imgs[1:], "duration": 100, "loop": 0}
    return encode(imgs[0], fmt, **params)


# --- Docker Compose (для тестов журнала, отказоустойчивости и сети) ---

class Compose:
    def __init__(self, command: str):
        self.base = shlex.split(command, posix=(os.name != "nt"))

    def run(self, *args: str, timeout: float = 120) -> subprocess.CompletedProcess:
        return subprocess.run([*self.base, *args], cwd=REPO_ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)

    def available(self) -> bool:
        try:
            return self.run("ps", "--services", "--filter", "status=running").returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def exec(self, service: str, *cmd: str, timeout: float = 60) -> subprocess.CompletedProcess:
        return self.run("exec", "-T", service, *cmd, timeout=timeout)

    def journal(self) -> list[dict]:
        res = self.exec("api", "cat", "/data/logs/journal.jsonl")
        if res.returncode != 0:
            raise RuntimeError(res.stderr.strip())
        out = []
        for line in res.stdout.splitlines():
            if line.strip():
                out.append({"_raw_len": len(line.encode()), **json.loads(line)})
        return out
