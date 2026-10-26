"""Приёмочные тесты по реестру требований (FR-001, FR-002, FR-005, FR-009, NFR-008).

Запуск при работающем сервисе:
    pip install -r tests/requirements.txt
    python tests/acceptance_test.py --url http://localhost:8000
"""
import argparse
import io
import sys
import time

import requests
from PIL import Image

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, details: str = "") -> None:
    RESULTS.append((name, ok, details))
    print(f"[{'OK' if ok else 'FAIL'}] {name}" + (f" — {details}" if details else ""))


def image_bytes(fmt: str, size=(320, 240), **params) -> bytes:
    img = Image.effect_noise(size, 64).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, fmt, **params)
    return buf.getvalue()


def upload(session: requests.Session, url: str, data: bytes, name: str, scale: int = 2):
    return session.post(f"{url}/api/jobs", files={"file": (name, data)}, data={"scale": scale}, timeout=60)


def wait_done(session: requests.Session, url: str, job_id: str, timeout: float) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = session.get(f"{url}/api/jobs/{job_id}", timeout=10).json()
        if job["status"] in ("done", "error", "rejected"):
            return job
        time.sleep(1)
    raise TimeoutError(f"задача {job_id} не завершилась за {timeout} с")


def check_queue_limit(url: str) -> None:
    """FR-005: при заполненной очереди следующая задача отклоняется.

    Ставит задачи из разных сессий, пока сервис не ответит отказом, затем удаляет их.
    """
    limit = requests.get(f"{url}/api/metrics", timeout=10).json()["queue"]["limit"]
    created, refused = [], None
    for i in range(limit + 5):
        s = requests.Session()
        res = upload(s, url, image_bytes("JPEG", (64, 64)), f"q{i}.jpg")
        if res.status_code == 202:
            created.append((s, res.json()["id"]))
        else:
            refused = res
            break
    in_flight = requests.get(f"{url}/api/metrics", timeout=10).json()["queue"]
    check("FR-005 лимит очереди", refused is not None and refused.status_code == 503
          and in_flight["length"] + in_flight["processing"] <= limit,
          f"принято {len(created)}, отказ HTTP {refused.status_code if refused is not None else '—'}, "
          f"лимит {limit}")
    for s, job_id in created:
        s.delete(f"{url}/api/jobs/{job_id}", timeout=10)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--timeout", type=float, default=600, help="ожидание одной обработки, с")
    parser.add_argument("--queue-limit", action="store_true",
                        help="проверить лимит очереди (лучше запускать на пустом сервисе)")
    args = parser.parse_args()
    url = args.url.rstrip("/")

    health = requests.get(f"{url}/api/health", timeout=10)
    if health.status_code != 200:
        print("Сервис не готов:", health.text)
        return 1
    print("Воркер:", health.json()["worker"])

    # FR-001: отклонение файлов вне ограничений (каждая проверка — в новой сессии)
    bad_files = {
        "размер > 10 МБ": (b"\xff\xd8" + b"0" * (10 * 1024 * 1024 + 1), "big.jpg", 413),
        "разрешение > 2000 px": (image_bytes("PNG", (2001, 32)), "wide.png", 400),
        "формат GIF": (image_bytes("GIF"), "anim.gif", 415),
        "повреждённый файл": (image_bytes("JPEG")[:500], "broken.jpg", 400),
        "не изображение": (b"hello, world", "text.png", 400),
    }
    for label, (data, name, status) in bad_files.items():
        res = upload(requests.Session(), url, data, name)
        check(f"FR-001 отклонение: {label}", res.status_code == status and "detail" in res.json(),
              f"HTTP {res.status_code}: {res.json().get('detail')}")

    # FR-001, FR-002, FR-009: допустимые форматы обрабатываются, размер результата = вход × масштаб
    cases = [("JPEG", "photo.jpg", 2), ("PNG", "photo.png", 4), ("WEBP", "photo.webp", 2)]
    sessions = {}
    for fmt, name, scale in cases:
        s = requests.Session()
        res = upload(s, url, image_bytes(fmt), name, scale)
        check(f"FR-001 приём {fmt}", res.status_code == 202, f"HTTP {res.status_code}")
        if res.status_code == 202:
            sessions[name] = (s, res.json()["id"], scale)

    first_name = next(iter(sessions), None)
    if first_name:
        s, job_id, _ = sessions[first_name]
        # FR-005: вторая задача той же сессии отклоняется, пока активна первая
        res = upload(s, url, image_bytes("JPEG"), "second.jpg")
        check("FR-005 вторая задача сессии отклонена", res.status_code == 429, f"HTTP {res.status_code}")
        # NFR-008: другая сессия не видит задачу и её файлы
        other = requests.Session()
        codes = [other.get(f"{url}/api/jobs/{job_id}{p}", timeout=10).status_code
                 for p in ("", "/download", "/preview/after")]
        check("NFR-008 изоляция сессий", all(c in (403, 404) for c in codes), f"коды {codes}")

    for name, (s, job_id, scale) in sessions.items():
        job = wait_done(s, url, job_id, args.timeout)
        if job["status"] != "done":
            check(f"FR-003 обработка {name}", False, job.get("error", job["status"]))
            continue
        res = s.get(f"{url}{job['files']['download']}", timeout=60)
        out = Image.open(io.BytesIO(res.content))
        expected = (job["width"] * scale, job["height"] * scale)
        check(f"FR-002 размер результата {name} ×{scale}", out.size == expected, f"{out.size} vs {expected}")
        check(f"FR-009 скачивание {name}", res.status_code == 200,
              f"обработка {job['result']['total_seconds']:.1f} с")
        # Удаление по запросу: после него ссылка недействительна
        s.delete(f"{url}/api/jobs/{job_id}", timeout=10)
        gone = s.get(f"{url}{job['files']['download']}", timeout=10).status_code
        check(f"FR-009 ссылка недействительна после удаления {name}", gone == 404, f"HTTP {gone}")

    if args.queue_limit:
        check_queue_limit(url)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\nИтого: {len(RESULTS) - len(failed)}/{len(RESULTS)} проверок пройдено")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
