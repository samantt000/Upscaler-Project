"""Adversarial testing (SEC-01, ЛР №4, задача 20): вредоносные и нестандартные входы, злоупотребления.

У апскейлера нет текстового промпта, поэтому атаки идут через файл и протокол:
файлы, ломающие декодер, маскировка содержимого, опасные имена файлов и обход лимитов.
"""
import io
import re

import pytest
from PIL import Image

import helpers as h

ICC_JUNK = b"\0" * 60000


def _html_png_name():
    return b"<html><body><script>alert(1)</script></body></html>"


def _svg():
    return (b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            b'<script>alert(1)</script><rect width="100" height="100"/></svg>')


# (описание, данные, имя файла, Content-Type или None)
CASES = [
    ("decompression bomb PNG 50000×50000", h.png_bomb(), "bomb.png", None),
    ("полиглот: JPEG + HTML в хвосте", h.noise() + b"<script>alert('xss')</script>", "poly.jpg", None),
    ("полиглот: PNG + ZIP в хвосте", h.noise(fmt="PNG") + b"PK\x03\x04" + b"\0" * 200, "poly.png", None),
    ("PNG под расширением .jpg", h.noise(fmt="PNG"), "fake.jpg", "image/jpeg"),
    ("SVG со скриптом под .png", _svg(), "vector.png", "image/png"),
    ("HTML под .png", _html_png_name(), "page.png", "image/png"),
    ("исполняемый заголовок MZ под .jpg", b"MZ\x90\x00" + b"\0" * 1000, "setup.jpg", "image/jpeg"),
    ("анимированный WEBP", h.animated("WEBP"), "anim.webp", None),
    ("анимированный PNG (APNG)", h.animated("PNG"), "anim.png", None),
    ("16-битный PNG", h.encode(Image.effect_noise((200, 150), 64).convert("I;16"), "PNG"), "deep.png", None),
    ("огромный ICC-профиль", h.encode(Image.effect_noise((200, 150), 64).convert("RGB"), "JPEG", icc_profile=ICC_JUNK), "icc.jpg", None),
    ("обход пути в имени файла", h.noise(), "../../../../tmp/evil_path.jpg", None),
    ("обход пути (Windows) в имени файла", h.noise(), "..\\..\\..\\evil_win.jpg", None),
    ("имя файла 5000 символов", h.noise(), "a" * 5000 + ".jpg", None),
    ("имя с HTML и юникодом", h.noise(), "<img src=x onerror=alert(1)>фото🙂.jpg", None),
    ("обрезанный PNG (нет IDAT до конца)", h.noise(fmt="PNG")[:5000], "cut.png", None),
]


@pytest.mark.tc("TC-SEC-01-01")
def test_sec01_malicious_files(api, note, request):
    outcomes, problems = {}, []
    for label, data, name, ctype in CASES:
        s = api.session()
        try:
            res = api.upload(s, data, name, 2, ctype)
        except Exception as e:  # noqa: BLE001
            problems.append(f"{label}: соединение оборвано ({type(e).__name__})")
            continue
        if res.status_code >= 500:
            problems.append(f"{label}: HTTP {res.status_code}")
            continue
        if res.status_code != 202:
            outcomes[label] = f"отклонён {res.status_code}"
            if "text/html" in res.headers.get("content-type", ""):
                problems.append(f"{label}: ответ об ошибке в HTML (возможное отражение)")
            continue
        job = api.wait(s, res.json()["id"])
        outcomes[label] = job["status"]
        if job["status"] == "error":
            problems.append(f"{label}: принят, но обработка упала ({job.get('error')})")
        if job["status"] == "done":
            dl = api.get(s, job["files"]["download"], timeout=120)
            disp = dl.headers.get("content-disposition", "")
            if b"<script" in dl.content or b"PK\x03\x04" in dl.content:
                problems.append(f"{label}: полезная нагрузка дошла до результата")
            if re.search(r"[/\\<>]|%2[fF]|%5[cC]|%3[cC]", disp.split("filename", 1)[-1]):
                problems.append(f"{label}: опасное имя в Content-Disposition: {disp[:120]}")
            Image.open(io.BytesIO(dl.content)).verify()
        api.delete(s, job["id"])

    health = api.health()
    if health.status_code != 200:
        problems.append(f"после атак сервис не готов: {health.text[:200]}")
    note("; ".join(f"{k} → {v}" for k, v in outcomes.items()))
    note(f"случаев: {len(CASES)}")

    # Файлы вне каталогов задач (нужен Docker; без него проверка пропускается)
    compose = h.Compose(request.config.getoption("--compose"))
    if compose.available():
        found = compose.exec("api", "find", "/data", "/tmp", "-name", "evil*")
        if found.stdout.strip():
            problems.append(f"файлы с именем из запроса созданы на диске: {found.stdout.strip()}")
    else:
        note("проверка файловой системы пропущена: Docker недоступен")
    assert not problems, "; ".join(problems)


@pytest.mark.tc("TC-SEC-01-02")
def test_sec01_cookie_rotation_cannot_fill_queue(api, note):
    """Один клиент без cookie (каждый запрос — новая сессия) не должен занять больше 2 мест
    в очереди (журнал решений, запись 8)."""
    owner = api.session()
    blocker = api.upload(owner, h.smooth((2000, 2000)), "blocker.jpg", 4)
    assert blocker.status_code == 202
    taken = []
    try:
        for i in range(6):
            s = api.session()
            res = api.upload(s, h.noise((64, 64)), f"flood{i}.jpg")
            if res.status_code == 202:
                taken.append((s, res.json()["id"]))
        total = len(taken) + 1
        note(f"одним клиентом занято мест: {total} из 7 попыток")
        assert total <= 2, f"одним клиентом занято {total} мест в очереди — лимита по IP нет"
    finally:
        for s, job_id in taken:
            api.delete(s, job_id)
        api.delete(owner, blocker.json()["id"])
