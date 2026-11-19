"""Настройки pytest для системных тестов ЛР №4.

Каждый тест помечен идентификатором тест-кейса: @pytest.mark.tc("TC-FR-001-01").
По итогам прогона результаты по тест-кейсам пишутся в results/lr4_results.csv —
из этого файла заполняется матрица трассируемости.
"""
import csv
import datetime
import os

import pytest

from helpers import REPO_ROOT, Api, Compose

RESULTS: dict[str, dict] = {}


def pytest_addoption(parser):
    g = parser.getgroup("lr4")
    g.addoption("--url", default="http://localhost:8000", help="адрес сервиса")
    g.addoption("--job-timeout", type=float, default=600, help="ожидание одной обработки, с")
    g.addoption("--compose", default="docker compose",
                help="команда Docker Compose (с -f ... для GPU-режима, если COMPOSE_FILE не задан в .env)")
    g.addoption("--portrait", default=None, help="путь к портрету (набор C) для проверки предупреждения о лицах")
    g.addoption("--results", default=os.path.join(REPO_ROOT, "results", "lr4_results.csv"))


def pytest_configure(config):
    config.addinivalue_line("markers", "tc(id): идентификатор тест-кейса")
    config.addinivalue_line("markers", "ops: требует доступа к Docker (перезапуск контейнеров, журнал)")
    config.addinivalue_line("markers", "retention: сервис запущен с RETENTION_SECONDS=120")
    config.addinivalue_line("markers", "offline: сервис запущен с docker-compose.offline.yml")


@pytest.fixture(scope="session")
def api(pytestconfig) -> Api:
    a = Api(pytestconfig.getoption("--url"), pytestconfig.getoption("--job-timeout"))
    res = a.health()
    if res.status_code != 200:
        pytest.exit(f"Сервис не готов: {res.text}", returncode=2)
    return a


@pytest.fixture(scope="session")
def compose(pytestconfig) -> Compose:
    c = Compose(pytestconfig.getoption("--compose"))
    if not c.available():
        pytest.skip("Docker Compose недоступен: запускайте из корня репозитория на ПК с сервисом")
    return c


@pytest.fixture(scope="session")
def worker_info(api) -> dict:
    return api.health().json()["worker"]


@pytest.fixture
def note(request):
    """Комментарий к результату тест-кейса (попадает в CSV и в вывод pytest -rA)."""
    lines: list[str] = []
    request.node.user_properties.append(("note", lines))
    return lines.append


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    marker = item.get_closest_marker("tc")
    if marker is None or (rep.when != "call" and not rep.skipped and not rep.failed):
        return
    notes = next((v for k, v in item.user_properties if k == "note"), [])
    status = "Pass" if rep.passed else "Skip" if rep.skipped else "Fail"
    detail = "; ".join(notes)
    if rep.failed and call.excinfo is not None:
        msg = str(call.excinfo.value).strip().splitlines()
        detail = (detail + "; " if detail else "") + (msg[0] if msg else call.excinfo.typename)
    if rep.skipped and isinstance(rep.longrepr, tuple):
        detail = rep.longrepr[2]
    for tc in marker.args:
        prev = RESULTS.get(tc)
        # Один тест-кейс может проверяться несколькими функциями: Fail имеет приоритет
        if prev and prev["status"] == "Fail":
            continue
        RESULTS[tc] = {"tc": tc, "status": status, "test": item.name, "detail": detail,
                       "time": datetime.datetime.now().strftime("%d.%m.%Y %H:%M")}


def pytest_sessionfinish(session):
    path = session.config.getoption("--results")
    if not RESULTS:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    old = {}
    if os.path.exists(path):  # результаты отдельных прогонов (ops, retention) дописываются
        with open(path, encoding="utf-8-sig") as f:
            old = {r["tc"]: r for r in csv.DictReader(f, delimiter=";")}
    old.update(RESULTS)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["tc", "status", "test", "detail", "time"], delimiter=";")
        w.writeheader()
        w.writerows(sorted(old.values(), key=lambda r: r["tc"]))
    print(f"\nРезультаты по тест-кейсам: {path}")
