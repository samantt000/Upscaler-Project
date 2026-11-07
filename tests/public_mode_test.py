"""Проверка режима демонстрации снаружи (NFR-008 в редакции CR-2026-004).

Запускать НЕ с ПК, где работает сервис, а с устройства во внешней сети
(ноутбук через мобильный интернет): многие роутеры не пропускают обращение
к собственному белому IP изнутри сети, и результат будет ложным.

    pip install -r tests/requirements.txt
    python tests/public_mode_test.py --domain upscaler-ivt.duckdns.org --key <ACCESS_KEY>
    # если провайдер закрыл 443 и сайт открыт на другом порту:
    python tests/public_mode_test.py --domain upscaler-ivt.duckdns.org --port 8443 --key <ACCESS_KEY>
"""
import argparse
import socket
import sys

import requests

RESULTS: list[tuple[str, bool, str]] = []
INTERNAL_PATHS = ("/metrics", "/api/metrics", "/api/docs", "/api/openapi.json", "/invite")
CLOSED_PORTS = (8000, 6379, 3389, 445)


def check(name: str, ok: bool, details: str = "") -> None:
    RESULTS.append((name, ok, details))
    print(f"[{'OK' if ok else 'FAIL'}] {name}" + (f" — {details}" if details else ""))


def port_closed(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=3):
            return False
    except OSError:
        return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", required=True, help="домен без https://, например upscaler-ivt.duckdns.org")
    parser.add_argument("--port", type=int, default=443, help="внешний порт HTTPS (PUBLIC_PORT из .env)")
    parser.add_argument("--key", required=True, help="ACCESS_KEY из .env")
    parser.add_argument("--insecure", action="store_true", help="не проверять сертификат (только для теста с localhost)")
    args = parser.parse_args()
    base = f"https://{args.domain}" + ("" if args.port == 443 else f":{args.port}")
    verify = not args.insecure
    if args.insecure:
        requests.packages.urllib3.disable_warnings()

    # Перенаправление с HTTP (только на стандартном порту: при нестандартном порт 80 не используется)
    if args.port != 443:
        print(f"[--] Перенаправление HTTP на HTTPS не проверяется: сайт работает на порту {args.port}")
    else:
        try:
            plain = requests.Session()
            plain.trust_env = verify
            res = plain.get(f"http://{args.domain}/", allow_redirects=False, timeout=10)
            check("HTTP перенаправляется на HTTPS", res.status_code in (301, 308)
                  and res.headers.get("Location", "").startswith("https://"), f"HTTP {res.status_code}")
        except requests.RequestException as e:
            check("HTTP перенаправляется на HTTPS", False, str(e))
    try:
        requests.get(f"{base}/healthz", timeout=10, verify=True)
        check("Сертификат HTTPS действителен", True)
    except requests.exceptions.SSLError as e:
        check("Сертификат HTTPS действителен", args.insecure, "пропущено (--insecure)" if args.insecure else str(e))
    except requests.RequestException as e:
        check("Сертификат HTTPS действителен", False, str(e))
        return 1

    # Доступ без ключа и с неверным ключом
    anon = requests.Session()
    anon.verify = verify
    anon.trust_env = verify  # иначе REQUESTS_CA_BUNDLE из окружения перебивает verify=False
    res = anon.get(f"{base}/", timeout=10)
    check("Без ключа: страница-приглашение", res.status_code == 403 and "приглашению" in res.text, f"HTTP {res.status_code}")
    res = anon.get(f"{base}/api/config", timeout=10)
    check("Без ключа: API закрыт", res.status_code == 401, f"HTTP {res.status_code}")
    res = anon.get(f"{base}/", params={"key": args.key + "x"}, timeout=10, allow_redirects=False)
    check("Неверный ключ отклонён", res.status_code == 403, f"HTTP {res.status_code}")

    # Вход по ключу
    s = requests.Session()
    s.verify = verify
    s.trust_env = verify  # иначе REQUESTS_CA_BUNDLE из окружения перебивает verify=False
    res = s.get(f"{base}/", params={"key": args.key}, timeout=10, allow_redirects=False)
    cookie = res.headers.get("Set-Cookie", "")
    check("Ссылка с ключом открывает сервис", res.status_code == 303 and "key=" not in res.headers.get("Location", ""),
          f"HTTP {res.status_code}, переход на {res.headers.get('Location')}")
    check("Cookie доступа: Secure и HttpOnly", "Secure" in cookie and "HttpOnly" in cookie, cookie.split(";")[0][:20] + "…")
    res = s.get(f"{base}/", timeout=10)
    check("С cookie открывается главная", res.status_code == 200 and "Апскейлер" in res.text, f"HTTP {res.status_code}")
    sid = next((c for c in s.cookies if c.name == "sid"), None)
    check("Cookie сессии sid: Secure", bool(sid and sid.secure), "есть" if sid else "cookie sid не получен")

    # Служебные страницы закрыты даже с ключом
    for path in INTERNAL_PATHS:
        code = s.get(f"{base}{path}", timeout=10).status_code
        check(f"Служебная страница {path} закрыта", code == 404, f"HTTP {code}")

    # Заголовки безопасности
    headers = s.get(f"{base}/", timeout=10).headers
    for name, expected in (("Strict-Transport-Security", "max-age="), ("X-Frame-Options", "DENY"),
                           ("Referrer-Policy", "no-referrer"), ("X-Content-Type-Options", "nosniff")):
        check(f"Заголовок {name}", expected in headers.get(name, ""), headers.get(name, "нет"))
    check("Версия сервера не раскрывается", "Server" not in headers, headers.get("Server", ""))

    # Лимит тела запроса на прокси
    res = s.post(f"{base}/api/jobs", files={"file": ("big.jpg", b"0" * (12 * 1024 * 1024))}, timeout=60)
    check("Запрос больше 11 МБ отсекается прокси", res.status_code == 413, f"HTTP {res.status_code}")

    # Из интернета открыты только 80 и 443
    for port in CLOSED_PORTS:
        check(f"Порт {port} закрыт снаружи", port_closed(args.domain, port))

    failed = [r for r in RESULTS if not r[1]]
    print(f"\nИтого: {len(RESULTS) - len(failed)}/{len(RESULTS)} проверок пройдено")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
