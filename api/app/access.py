"""Доступ по ссылке-приглашению в режиме демонстрации (CR-2026-004).

Если задан ACCESS_KEY, сервис открывается только по ссылке вида /?key=<ключ>:
ключ проверяется, в cookie сохраняется его хеш, и браузер перенаправляется на адрес
без ключа (чтобы ключ не оставался в истории и адресной строке). После смены
ACCESS_KEY старые ссылки и cookie перестают действовать.
"""
import hashlib
import html
import os
import re
import secrets
from urllib.parse import urlencode

import qrcode
import qrcode.image.svg
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import config

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
COOKIE_MAX_AGE = 7 * 24 * 3600

# Без ключа доступны только статика (стили страницы-приглашения) и проверка живости
OPEN_PATHS = ("/healthz",)
OPEN_PREFIXES = ("/static/",)

WRONG_KEY_MESSAGE = (
    '<p class="error" role="alert">Ссылка устарела или ключ указан с ошибкой. '
    "Попросите у автора новую ссылку или QR-код.</p>"
)


def _template(name: str) -> str:
    with open(os.path.join(TEMPLATES_DIR, name), encoding="utf-8") as f:
        return f.read()


INVITE_PAGE = _template("invite.html")
SHARE_PAGE = _template("share.html")


def is_https(request: Request) -> bool:
    # За Caddy схема берётся из X-Forwarded-Proto (uvicorn запущен с --proxy-headers)
    return request.url.scheme == "https"


def _token(key: str) -> str:
    return hashlib.sha256(f"upscaler-access:{key}".encode()).hexdigest()


def _matches(value: str, expected: str) -> bool:
    return secrets.compare_digest(value.encode(), expected.encode())


def _invite(wrong_key: bool) -> HTMLResponse:
    body = INVITE_PAGE.replace("{{message}}", WRONG_KEY_MESSAGE if wrong_key else "")
    return HTMLResponse(body, status_code=403, headers={"Cache-Control": "no-store"})


async def gate(request: Request, call_next):
    key = config.ACCESS_KEY
    path = request.url.path
    if not key or path in OPEN_PATHS or path.startswith(OPEN_PREFIXES):
        return await call_next(request)

    supplied = request.query_params.get("key")
    if supplied is not None:
        if not _matches(supplied, key):
            return _invite(wrong_key=True)
        rest = [(k, v) for k, v in request.query_params.multi_items() if k != "key"]
        response = RedirectResponse(path + (f"?{urlencode(rest)}" if rest else ""), status_code=303)
        response.set_cookie(config.ACCESS_COOKIE, _token(key), max_age=COOKIE_MAX_AGE,
                            httponly=True, samesite="lax", secure=is_https(request))
        return response

    if _matches(request.cookies.get(config.ACCESS_COOKIE, ""), _token(key)):
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Доступ только по ссылке-приглашению"}, status_code=401)
    return _invite(wrong_key=False)


def share_page() -> HTMLResponse | None:
    """Страница с QR-кодом приглашения для слайда; только в режиме демонстрации."""
    if not (config.ACCESS_KEY and config.PUBLIC_URL):
        return None
    link = f"{config.PUBLIC_URL}/?key={config.ACCESS_KEY}"
    svg = qrcode.make(link, image_factory=qrcode.image.svg.SvgPathImage, border=2,
                      box_size=10).to_string(encoding="unicode")
    # Библиотека задаёт размер в мм; ставим пиксели, чтобы место под код было зарезервировано
    svg = re.sub(r'\s(?:width|height)="[^"]*"', "", svg, count=2)
    svg = svg.replace("<svg ", '<svg width="280" height="280" role="img" aria-labelledby="qr-title" ', 1)
    body = SHARE_PAGE.replace("{{qr}}", svg).replace("{{link}}", html.escape(link, quote=True))
    return HTMLResponse(body, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
