"""Тест-кейсы, которым нужен доступ к Docker на ПК с сервисом (ЛР №4).

  TC-FR-009-02  отказоустойчивость: результат доступен после перезапуска redis   (-m ops)
  TC-FR-010-01  автоудаление с ускоренным сроком хранения                          (-m retention)
  TC-CON-005-01 обработка без выхода в интернет, нет исходящих соединений          (-m offline)

Запуск — см. tests/lr4/README.md: для retention и offline сервис перезапускается
с другими настройками, поэтому эти тесты по умолчанию пропускаются.
"""
import ipaddress
import threading
import time

import pytest

import helpers as h


@pytest.mark.ops
@pytest.mark.tc("TC-FR-009-02")
def test_fr009_result_survives_redis_restart(api, compose, note):
    s = api.session()
    res = api.upload(s, h.noise(), "restart.jpg")
    job = api.wait(s, res.json()["id"])
    assert job["status"] == "done"
    link = job["files"]["download"]
    assert api.get(s, link).status_code == 200

    r = compose.run("restart", "redis", timeout=120)
    assert r.returncode == 0, r.stderr
    t0 = time.time()
    ready = api.wait_ready(300)
    note(f"сервис готов через {time.time() - t0:.0f} с после перезапуска redis" if ready
         else "сервис не вернулся в ready за 300 с")
    assert ready
    status = api.get(s, f"/api/jobs/{job['id']}").status_code
    download = api.get(s, link).status_code
    exists = compose.exec("api", "test", "-d", f"/data/jobs/{job['id']}").returncode == 0
    note(f"после перезапуска: статус HTTP {status}, скачивание HTTP {download}, файлы на диске: {'да' if exists else 'нет'}")
    try:
        assert status == 200 and download == 200, \
            "после перезапуска redis задача и ссылка недоступны (состояние задач не сохранилось)"
    finally:
        if status == 200:
            api.delete(s, job["id"])


@pytest.mark.retention
@pytest.mark.tc("TC-FR-010-01")
def test_fr010_files_removed_after_retention(api, compose, note):
    retention = api.get(api.session(), "/api/config").json()["retention_hours"] * 3600
    if retention > 600:
        pytest.skip(f"срок хранения {retention:.0f} с: перезапустите сервис с RETENTION_SECONDS=120")
    env = compose.exec("worker", "printenv", "CLEANUP_INTERVAL_SECONDS")
    interval = float(env.stdout.strip() or 600)
    s = api.session()
    res = api.upload(s, h.noise(), "retention.jpg")
    job = api.wait(s, res.json()["id"])
    assert job["status"] == "done"
    created, link = job["created"], job["files"]["download"]
    deadline = created + retention + interval + 15

    gone_at = None
    while time.time() < deadline + 60:
        if api.get(s, link).status_code == 404:
            gone_at = time.time()
            break
        time.sleep(3)
    assert gone_at is not None, "файл не удалён и через срок хранения + интервал очистки + 75 с"
    lifetime = gone_at - created
    note(f"срок хранения {retention:.0f} с, интервал очистки {interval:.0f} с, файл удалён через {lifetime:.0f} с")
    on_disk = compose.exec("api", "test", "-e", f"/data/jobs/{job['id']}").returncode == 0
    events = [e for e in compose.journal() if e["event"] == "cleanup" and e["ts"] >= created]
    assert not on_disk, "каталог задачи остался на диске"
    assert events, "в журнале нет события cleanup"
    assert gone_at <= deadline, f"удалено через {lifetime:.0f} с, предел {retention + interval + 15:.0f} с"


def _remote_addresses(proc_text: str, v6: bool) -> set[str]:
    out = set()
    for line in proc_text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4 or parts[3] == "0A":  # 0A — LISTEN
            continue
        hex_ip = parts[2].split(":")[0]
        if v6:
            words = [hex_ip[i:i + 8] for i in range(0, 32, 8)]
            raw = b"".join(bytes.fromhex(w)[::-1] for w in words)
            out.add(str(ipaddress.IPv6Address(raw)))
        else:
            out.add(str(ipaddress.IPv4Address(bytes.fromhex(hex_ip)[::-1])))
    return out


def _is_local(addr: str) -> bool:
    ip = ipaddress.ip_address(addr)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_unspecified or ip.is_link_local


@pytest.mark.offline
@pytest.mark.tc("TC-CON-005-01")
def test_con005_no_external_connections(api, compose, note):
    probe = compose.exec("worker", "python", "-c",
                         "import socket; socket.create_connection(('1.1.1.1', 443), 5)")
    if probe.returncode == 0:
        pytest.skip("у воркера есть выход в интернет: запустите сервис с docker-compose.offline.yml")
    note("выход воркера в интернет закрыт (сеть internal)")

    seen: dict[str, set[str]] = {"api": set(), "worker": set()}
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            for svc in seen:
                for name, v6 in (("tcp", False), ("tcp6", True)):
                    r = compose.exec(svc, "cat", f"/proc/net/{name}", timeout=20)
                    if r.returncode == 0:
                        seen[svc] |= _remote_addresses(r.stdout, v6)
            time.sleep(0.5)

    t = threading.Thread(target=sample, daemon=True)
    t.start()
    try:
        jobs = []
        for i in range(3):
            s = api.session()
            res = api.upload(s, h.noise((400, 300)), f"offline{i}.jpg", 4)
            assert res.status_code == 202
            jobs.append((s, res.json()["id"]))
        results = [api.wait(s, j)["status"] for s, j in jobs]
    finally:
        stop.set()
        t.join(30)
    for s, j in jobs:
        api.delete(s, j)
    external = {svc: sorted(a for a in addrs if not _is_local(a)) for svc, addrs in seen.items()}
    note(f"удалённые адреса: api {sorted(seen['api'])}, worker {sorted(seen['worker'])}")
    assert results == ["done"] * 3, f"обработка без интернета: {results}"
    assert not any(external.values()), f"исходящие соединения во внешние адреса: {external}"
