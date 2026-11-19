"""Журнал и метрики (FR-011, FR-012). Файл называется test_z_*, чтобы выполняться последним:
к этому моменту предыдущие тесты создают в журнале несколько десятков обработок."""
import pytest

MIN_JOBS = 20


@pytest.fixture(scope="module")
def journal(compose):
    return compose.journal()


@pytest.mark.tc("TC-FR-011-01")
def test_fr011_metrics_and_journal(api, journal, note):
    m = api.metrics()
    missing = []
    checks = {
        "длина очереди": "length" in m["queue"],
        "время p50/p95": any({"p50", "p95"} <= set(v) for v in m["processing_time"].values()),
        "доля ошибок": m["jobs"]["error_rate"] is not None,
        "распределение разрешений": bool(m["inputs"]["resolutions"]),
        "распределение форматов": bool(m["inputs"]["formats"]),
        "версия модели": bool(m["models"]),
        "срабатывание детектора лиц": "jobs_with_faces" in m["faces"],
    }
    missing = [k for k, ok in checks.items() if not ok]
    done = m["jobs"]["done"]
    note(f"обработок в журнале: {done}; p95 по устройствам: "
         + ", ".join(f"{d}: {v['p95']} с" for d, v in m["processing_time"].items()))
    assert done >= MIN_JOBS, f"в журнале {done} обработок, нужно ≥{MIN_JOBS}"
    assert not missing, "нет показателей: " + ", ".join(missing)

    # Изображения в журнал не пишутся: короткие записи, без длинных строк (base64) и бинарных полей
    long_lines = [e["event"] for e in journal if e["_raw_len"] > 2048]
    long_values = [(e["event"], k) for e in journal for k, v in e.items()
                   if isinstance(v, str) and len(v) > 256]
    note(f"записей журнала: {len(journal)}, самая длинная {max(e['_raw_len'] for e in journal)} байт")
    assert not long_lines and not long_values, f"подозрительные записи: {long_lines[:5]} {long_values[:5]}"


@pytest.mark.tc("TC-FR-012-01")
def test_fr012_brisque_logged_for_every_job(journal, note):
    done = [e for e in journal if e["event"] == "job_done"]
    without = [e["job_id"] for e in done if not isinstance(e.get("brisque"), (int, float))]
    session = [e.get("brisque_session", {}) for e in done]
    has_baseline = any("baseline" in s for s in session)
    note(f"job_done: {len(done)}, без BRISQUE: {len(without)}; базовая линия задана: {'да' if has_baseline else 'нет'}")
    assert len(done) >= MIN_JOBS
    assert not without, f"нет BRISQUE у задач: {without[:5]}"
