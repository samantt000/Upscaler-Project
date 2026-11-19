"""Функциональные тест-кейсы: FR-001…FR-005, FR-009, NFR-008 (ЛР №4, задача 18).

Нормальные сценарии, граничные условия и исключительные ситуации.
"""
import io

import pytest
from PIL import Image

import helpers as h

MB = 1024 * 1024


def submit(api, data, name, scale=2):
    """Загрузка в новой сессии (у каждой сессии может быть только одна активная задача)."""
    s = api.session()
    return s, api.upload(s, data, name, scale)


def accepted_then_cleanup(api, s, res):
    if res.status_code == 202:
        api.delete(s, res.json()["id"])


# --- FR-001: загрузка файла ---

@pytest.mark.tc("TC-FR-001-01")
def test_fr001_accepts_allowed_formats(api, note):
    """Нормальный сценарий: JPG, PNG, WEBP приняты и обработаны."""
    for fmt, name in (("JPEG", "photo.jpg"), ("PNG", "photo.png"), ("WEBP", "photo.webp")):
        s, res = submit(api, h.noise(fmt=fmt), name)
        assert res.status_code == 202, f"{fmt}: HTTP {res.status_code} {res.text[:200]}"
        job = api.wait(s, res.json()["id"])
        assert job["status"] == "done", f"{fmt}: {job}"
        note(f"{fmt}: {job['result']['total_seconds']} с")
        api.delete(s, job["id"])


@pytest.mark.tc("TC-FR-001-02")
@pytest.mark.parametrize("size, expected", [
    ((2000, 2000), 202), ((2001, 2000), 400), ((2000, 2001), 400),
    ((16, 16), 202), ((15, 16), 400), ((16, 15), 400),
])
def test_fr001_resolution_boundaries(api, size, expected):
    """Граничные условия разрешения: 2000 и 16 px включительно."""
    s, res = submit(api, h.smooth(size, "PNG" if size[0] < 100 else "JPEG"), "edge.png")
    try:
        assert res.status_code == expected, f"{size}: HTTP {res.status_code} {res.text[:200]}"
        if expected != 202:
            assert res.json().get("detail"), "нет текста ошибки"
    finally:
        accepted_then_cleanup(api, s, res)


@pytest.mark.tc("TC-FR-001-03")
@pytest.mark.parametrize("label, data, name, expected", [
    ("GIF", h.noise(fmt="GIF"), "anim.gif", 415),
    ("BMP", h.noise(fmt="BMP"), "img.bmp", 415),
    ("TIFF", h.noise(fmt="TIFF"), "img.tif", 415),
    ("обрезанный JPEG", h.noise()[:2000], "broken.jpg", 400),
    ("текст вместо картинки", b"hello, world", "text.png", 400),
    ("пустой файл", b"", "empty.jpg", 400),
], ids=["gif", "bmp", "tiff", "truncated-jpeg", "text", "empty"])
def test_fr001_rejects_invalid(api, label, data, name, expected):
    """Исключительные ситуации: неподдерживаемый формат и повреждённые файлы."""
    s, res = submit(api, data, name)
    try:
        assert res.status_code == expected, f"{label}: HTTP {res.status_code} {res.text[:200]}"
        assert res.json().get("detail"), f"{label}: нет текста ошибки"
    finally:
        accepted_then_cleanup(api, s, res)


@pytest.mark.tc("TC-FR-001-04")
@pytest.mark.parametrize("size, expected", [(10 * MB, 202), (10 * MB + 1, 413)])
def test_fr001_file_size_boundary(api, size, expected):
    """Граница размера: ровно 10 МБ принимается, на 1 байт больше — 413."""
    data = h.png_exact_size(size)
    assert len(data) == size
    s, res = submit(api, data, "size.png")
    try:
        assert res.status_code == expected, f"{size} байт: HTTP {res.status_code} {res.text[:200]}"
    finally:
        accepted_then_cleanup(api, s, res)


# --- FR-002: выбор масштаба ---

@pytest.mark.tc("TC-FR-002-01")
def test_fr002_result_size_matches_scale(api, note):
    """Размер результата = размер входа × коэффициент (5 изображений × 2 коэффициента)."""
    sizes = [(320, 240), (240, 320), (257, 131), (64, 64), (500, 375)]
    jobs = []
    for size in sizes:
        for scale in (2, 4):
            s, res = submit(api, h.noise(size, "PNG"), f"s{size[0]}x{size[1]}.png", scale)
            assert res.status_code == 202, f"{size} ×{scale}: HTTP {res.status_code}"
            jobs.append((s, res.json()["id"], size, scale))
    bad = []
    for s, job_id, size, scale in jobs:
        job = api.wait(s, job_id)
        assert job["status"] == "done", f"{size} ×{scale}: {job}"
        out = Image.open(io.BytesIO(api.get(s, job["files"]["download"], timeout=120).content)).size
        if out != (size[0] * scale, size[1] * scale):
            bad.append(f"{size}×{scale} → {out}")
        api.delete(s, job_id)
    note(f"проверено {len(jobs)} результатов")
    assert not bad, "; ".join(bad)


@pytest.mark.tc("TC-FR-002-02")
@pytest.mark.parametrize("scale", [3, 8, 1])
def test_fr002_rejects_unsupported_scale(api, scale):
    s, res = submit(api, h.noise(), "scale.jpg", scale)
    try:
        assert res.status_code == 400 and res.json().get("reason") == "scale", f"HTTP {res.status_code} {res.text[:200]}"
    finally:
        accepted_then_cleanup(api, s, res)


# --- FR-003: обработка нетипичных входов ---

def _rgba_png():
    img = Image.effect_noise((200, 150), 64).convert("RGBA")
    img.putalpha(Image.linear_gradient("L").resize((200, 150)))
    return h.encode(img, "PNG")


ATYPICAL = {
    "прозрачность (RGBA PNG)": (_rgba_png(), "alpha.png", (200, 150)),
    "оттенки серого": (h.encode(Image.effect_noise((200, 150), 64), "JPEG"), "gray.jpg", (200, 150)),
    "палитра (P PNG)": (h.encode(Image.effect_noise((200, 150), 64).convert("RGB").quantize(64), "PNG"), "pal.png", (200, 150)),
    "CMYK JPEG": (h.encode(Image.effect_noise((200, 150), 64).convert("RGB").convert("CMYK"), "JPEG"), "cmyk.jpg", (200, 150)),
    "EXIF-поворот на 90°": (h.jpeg_with_orientation((320, 240), 6), "rotated.jpg", (240, 320)),
    "минимальный 16×16": (h.noise((16, 16), "PNG"), "tiny.png", (16, 16)),
}


@pytest.mark.tc("TC-FR-003-01")
@pytest.mark.parametrize("label", list(ATYPICAL), ids=["rgba", "gray", "palette", "cmyk", "exif-rotate", "min-16px"])
def test_fr003_atypical_inputs(api, label, note):
    data, name, display_size = ATYPICAL[label]
    s, res = submit(api, data, name, 2)
    assert res.status_code == 202, f"HTTP {res.status_code} {res.text[:200]}"
    job = api.wait(s, res.json()["id"])
    try:
        assert job["status"] == "done", job.get("error", job["status"])
        dl = api.get(s, job["files"]["download"], timeout=120)
        assert dl.status_code == 200
        out = Image.open(io.BytesIO(dl.content)).size
        assert out == (display_size[0] * 2, display_size[1] * 2), f"размер {out}, ожидалось {display_size}×2"
        if (job["width"], job["height"]) != display_size:
            note(f"API сообщает размер входа {job['width']}×{job['height']} без учёта EXIF-поворота")
    finally:
        api.delete(s, job["id"])


# --- FR-004: объяснимость (предупреждения соответствуют результату) ---

WARN = {
    "faces": "Обнаружено лиц",
    "zone": "Зона активной генерации",
    "detail": "Высокий индекс достройки",
    "small": "меньше 64 px",
    "alpha": "Прозрачность не поддерживается",
}


@pytest.mark.tc("TC-FR-004-02")
def test_fr004_explainability_warnings(api, pytestconfig, note):
    """Каждое условие сопровождается своим предупреждением, и лишних предупреждений нет;
    карта изменений и превью «до/после» доступны."""
    inputs = {"small.png": h.noise((48, 40), "PNG"), "alpha.png": _rgba_png(),
              "noise.jpg": h.noise((320, 240)), "smooth.jpg": h.smooth((320, 240))}
    portrait = pytestconfig.getoption("--portrait")
    if portrait:
        with open(portrait, "rb") as f:
            inputs["portrait.png"] = f.read()
    else:
        note("портрет не задан (--portrait): условие «лица» проверяется только на отсутствие ложных предупреждений")
    problems, seen = [], set()
    for name, data in inputs.items():
        s, res = submit(api, data, name, 4)
        assert res.status_code == 202, f"{name}: HTTP {res.status_code}"
        job = api.wait(s, res.json()["id"])
        assert job["status"] == "done", f"{name}: {job}"
        r, text = job["result"], " ".join(job["result"]["warnings"])
        expected = {
            "faces": r["faces"] > 0,
            "zone": r["flag_face_zone"],
            "detail": r["detail_index"] > 0.35,
            "small": min(r["width"], r["height"]) < 64,
            "alpha": name == "alpha.png",
        }
        for key, cond in expected.items():
            if cond:
                seen.add(key)
            if cond != (WARN[key] in text):
                problems.append(f"{name}: условие «{key}»={cond}, предупреждение={'есть' if not cond else 'нет'}")
        for kind in ("before", "after", "diff"):
            code = api.get(s, job["files"][kind]).status_code
            if code != 200:
                problems.append(f"{name}: превью {kind} HTTP {code}")
        api.delete(s, job["id"])
    note("сработали условия: " + ", ".join(sorted(seen)))
    if portrait:
        assert "faces" in seen, "на портрете не найдено лицо — проверка предупреждения не состоялась"
    assert not problems, "; ".join(problems)


# --- FR-005: сессия и очередь ---

@pytest.mark.tc("TC-FR-005-01")
def test_fr005_one_active_job_per_session(api):
    s = api.session()
    first = api.upload(s, h.smooth((2000, 2000)), "lock.jpg", 4)
    assert first.status_code == 202
    job_id = first.json()["id"]
    second = api.upload(s, h.noise(), "second.jpg")
    state = api.status(s, job_id)["status"]
    try:
        if second.status_code == 202 and state == "done":
            pytest.skip("первая задача завершилась раньше второго запроса — проверка не состоялась")
        assert second.status_code == 429 and second.json()["reason"] == "session_busy", \
            f"HTTP {second.status_code}, первая задача: {state}"
    finally:
        if second.status_code == 202:
            api.delete(s, second.json()["id"])
        api.delete(s, job_id)


@pytest.fixture
def blocker(api):
    """Тяжёлая задача (2000×2000, ×4) занимает воркер, чтобы очередь не успевала разбираться."""
    s = api.session()
    res = api.upload(s, h.smooth((2000, 2000)), "blocker.jpg", 4)
    assert res.status_code == 202
    yield s, res.json()["id"]
    api.delete(s, res.json()["id"])


@pytest.mark.tc("TC-FR-005-02")
def test_fr005_queue_limit(api, blocker, note):
    limit = api.metrics()["queue"]["limit"]
    created, refused = [], None
    try:
        for i in range(limit + 5):
            s, res = submit(api, h.noise((64, 64)), f"q{i}.jpg")
            if res.status_code == 202:
                created.append((s, res.json()["id"]))
            else:
                refused = res
                break
        q = api.metrics()["queue"]
        note(f"принято {len(created) + 1} с учётом блокирующей, лимит {limit}")
        assert refused is not None, "очередь не отказала ни разу"
        assert refused.status_code == 503 and refused.json()["reason"] == "queue_full", \
            f"отказ HTTP {refused.status_code} {refused.text[:200]}"
        assert q["length"] + q["processing"] <= limit, f"в очереди {q}"
    finally:
        for s, job_id in created:
            api.delete(s, job_id)


# --- FR-009: выдача результата ---

@pytest.mark.tc("TC-FR-009-01")
def test_fr009_download_until_deleted(api):
    s, res = submit(api, h.noise(), "dl.jpg")
    job = api.wait(s, res.json()["id"])
    assert job["status"] == "done"
    link = job["files"]["download"]
    dl = api.get(s, link)
    assert dl.status_code == 200 and len(dl.content) > 0
    assert "attachment" in dl.headers.get("content-disposition", "")
    api.delete(s, job["id"])
    assert api.get(s, link).status_code == 404, "ссылка работает после удаления"
    assert api.get(s, f"/api/jobs/{job['id']}").status_code == 404


# --- NFR-008: изоляция сессий ---

@pytest.mark.tc("TC-NFR-008-01")
def test_nfr008_other_session_cannot_access(api):
    s, res = submit(api, h.noise(), "private.jpg")
    job = api.wait(s, res.json()["id"])
    other = api.session()
    paths = ["", "/download", "/preview/before", "/preview/after", "/preview/diff"]
    codes = {p or "/": api.get(other, f"/api/jobs/{job['id']}{p}").status_code for p in paths}
    deleted = other.delete(f"{api.url}/api/jobs/{job['id']}", timeout=30).status_code
    still_there = api.get(s, f"/api/jobs/{job['id']}").status_code
    api.delete(s, job["id"])
    assert all(c == 404 for c in codes.values()), f"коды для чужой сессии: {codes}"
    assert deleted == 404 and still_there == 200, f"удаление чужой сессией: HTTP {deleted}, задача у владельца: {still_there}"
