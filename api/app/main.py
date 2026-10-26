"""HTTP API и раздача веб-интерфейса."""
import hashlib
import json
import os
import re
import secrets
import shutil
import time
import uuid

import redis
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, journal, metrics
from .validation import InvalidImage, validate

QUEUE_KEY = "queue:jobs"
PROCESSING_KEY = "jobs:processing"
HEARTBEAT_KEY = "worker:heartbeat"

# Атомарная постановка в очередь (FR-005): проверка активной задачи сессии и лимита очереди
ENQUEUE = """
if redis.call('EXISTS', KEYS[3]) == 1 then return -1 end
if redis.call('LLEN', KEYS[1]) + redis.call('SCARD', KEYS[2]) >= tonumber(ARGV[2]) then return -2 end
redis.call('SET', KEYS[3], ARGV[1], 'EX', ARGV[3])
redis.call('RPUSH', KEYS[1], ARGV[1])
return 1
"""
RELEASE_SESSION = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
PREVIEWS = {"before": "preview_before.jpg", "after": "preview_after.jpg", "diff": "preview_diff.jpg"}

r = redis.Redis.from_url(config.REDIS_URL, decode_responses=True)
enqueue_script = r.register_script(ENQUEUE)
release_script = r.register_script(RELEASE_SESSION)

app = FastAPI(title="Апскейлер", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --- анонимная сессия (FR-005) ---

@app.middleware("http")
async def session_cookie(request: Request, call_next):
    sid = request.cookies.get(config.SESSION_COOKIE)
    is_new = not sid or not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", sid)
    if is_new:
        sid = secrets.token_urlsafe(24)
    request.state.sid = sid
    response = await call_next(request)
    if is_new:
        response.set_cookie(config.SESSION_COOKIE, sid, httponly=True, samesite="lax",
                            max_age=30 * 24 * 3600)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def session_hash(sid: str) -> str:
    """Обезличенный идентификатор сессии для журнала."""
    return hashlib.sha256(sid.encode()).hexdigest()[:12]


def job_dir(job_id: str) -> str:
    return os.path.join(config.DATA_DIR, "jobs", job_id)


def get_own_job(request: Request, job_id: str) -> dict:
    """NFR-008: задача другой сессии неотличима от несуществующей (404)."""
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(404, "Задача не найдена")
    job = r.hgetall(f"job:{job_id}")
    if not job or not secrets.compare_digest(job.get("session", ""), request.state.sid):
        raise HTTPException(404, "Задача не найдена")
    return job


def reject(request: Request, status: int, reason: str, message: str):
    journal.write("upload_rejected", session=session_hash(request.state.sid), reason=reason)
    return JSONResponse({"detail": message, "reason": reason}, status_code=status)


def safe_stem(filename: str | None) -> str:
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    stem = re.sub(r"[^\w\-. ]+", "", stem, flags=re.UNICODE).strip()[:60]
    return stem or "image"


# --- страницы ---

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/metrics", include_in_schema=False)
def metrics_page():
    return FileResponse(os.path.join(STATIC_DIR, "metrics.html"))


# --- API ---

@app.get("/api/config")
def client_config():
    return {
        "max_file_mb": config.MAX_FILE_BYTES // (1024 * 1024),
        "max_side": config.MAX_SIDE,
        "formats": ["JPG", "PNG", "WEBP"],
        "scales": list(config.ALLOWED_SCALES),
        "retention_hours": round(config.RETENTION_SECONDS / 3600, 2),
    }


@app.get("/api/health")
def health():
    """Готовность сервиса (NFR-013): 200, когда воркер загрузил модель."""
    try:
        r.ping()
    except redis.RedisError:
        return JSONResponse({"status": "down", "redis": False, "worker": None}, status_code=503)
    raw = r.get(HEARTBEAT_KEY)
    worker = json.loads(raw) if raw else None
    ready = bool(worker and worker.get("ready"))
    return JSONResponse({"status": "ready" if ready else "starting", "redis": True, "worker": worker},
                        status_code=200 if ready else 503)


@app.post("/api/jobs", status_code=202)
async def create_job(request: Request, file: UploadFile = File(...), scale: int = Form(4)):
    sid = request.state.sid
    if scale not in config.ALLOWED_SCALES:
        return reject(request, 400, "scale", "Коэффициент увеличения должен быть ×2 или ×4")
    if r.exists(f"session:{sid}:active"):
        return reject(request, 429, "session_busy",
                      "У вас уже есть активная задача. Дождитесь её завершения.")

    data = await file.read(config.MAX_FILE_BYTES + 1)
    try:
        ext, w, h = await run_in_threadpool(validate, data)
    except InvalidImage as e:
        return reject(request, e.status, "invalid_file", str(e))

    job_id = uuid.uuid4().hex
    path = job_dir(job_id)
    os.makedirs(path)
    with open(os.path.join(path, f"input.{ext}"), "wb") as f:
        f.write(data)

    now = time.time()
    key = f"job:{job_id}"
    r.hset(key, mapping={
        "id": job_id, "session": sid, "session_hash": session_hash(sid), "status": "queued",
        "progress": 0, "scale": scale, "ext": ext, "filename": safe_stem(file.filename),
        "width": w, "height": h, "created": now,
    })
    r.expire(key, config.RETENTION_SECONDS + 3600)

    code = enqueue_script(keys=[QUEUE_KEY, PROCESSING_KEY, f"session:{sid}:active"],
                          args=[job_id, config.QUEUE_LIMIT, config.SESSION_LOCK_TTL])
    if code != 1:
        r.delete(key)
        shutil.rmtree(path, ignore_errors=True)
        if code == -1:
            return reject(request, 429, "session_busy",
                          "У вас уже есть активная задача. Дождитесь её завершения.")
        return reject(request, 503, "queue_full",
                      "Сервис перегружен: очередь заполнена. Попробуйте через несколько минут.")
    return {"id": job_id}


@app.get("/api/session")
def session_state(request: Request):
    """Активная задача сессии — чтобы после перезагрузки страницы продолжить отслеживание."""
    return {"active_job": r.get(f"session:{request.state.sid}:active")}


@app.get("/api/jobs/{job_id}")
def job_status(request: Request, job_id: str):
    job = get_own_job(request, job_id)
    created = float(job["created"])
    body = {
        "id": job_id,
        "status": job["status"],
        "progress": float(job.get("progress", 0)),
        "scale": int(job["scale"]),
        "width": int(job["width"]),
        "height": int(job["height"]),
        "created": created,
        "expires_at": created + config.RETENTION_SECONDS,
    }
    if job["status"] == "queued":
        pos = r.lpos(QUEUE_KEY, job_id)
        body["position"] = pos + 1 if pos is not None else None
    if job.get("error"):
        body["error"] = job["error"]
    if job["status"] == "done":
        body["result"] = json.loads(job["result"])
        base = f"/api/jobs/{job_id}"
        body["files"] = {**{k: f"{base}/preview/{k}" for k in PREVIEWS}, "download": f"{base}/download"}
    return body


@app.get("/api/jobs/{job_id}/preview/{kind}")
def job_preview(request: Request, job_id: str, kind: str):
    get_own_job(request, job_id)
    if kind not in PREVIEWS:
        raise HTTPException(404, "Нет такого файла")
    path = os.path.join(job_dir(job_id), PREVIEWS[kind])
    if not os.path.exists(path):
        raise HTTPException(404, "Файл удалён или ещё не готов")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/jobs/{job_id}/download")
def job_download(request: Request, job_id: str):
    """FR-009: выдача результата по ссылке до автоудаления."""
    job = get_own_job(request, job_id)
    path = os.path.join(job_dir(job_id), f"result.{job['ext']}")
    if job["status"] != "done" or not os.path.exists(path):
        raise HTTPException(404, "Результат удалён или ещё не готов")
    journal.write("downloaded", job_id=job_id, session=job["session_hash"])
    name = f"{job['filename']}_x{job['scale']}.{job['ext']}"
    return FileResponse(path, filename=name)


@app.delete("/api/jobs/{job_id}", status_code=204)
def job_delete(request: Request, job_id: str):
    """Удаление файлов по запросу пользователя, не дожидаясь 24 часов (152-ФЗ)."""
    job = get_own_job(request, job_id)
    if job["status"] == "processing":
        raise HTTPException(409, "Задача обрабатывается — удалить её можно после завершения")
    r.lrem(QUEUE_KEY, 0, job_id)
    release_script(keys=[f"session:{job['session']}:active"], args=[job_id])
    r.delete(f"job:{job_id}")
    shutil.rmtree(job_dir(job_id), ignore_errors=True)
    journal.write("deleted_by_user", job_id=job_id, session=job["session_hash"])


@app.get("/api/metrics")
def metrics_data():
    raw = r.get(HEARTBEAT_KEY)
    return metrics.summary(r.llen(QUEUE_KEY), r.scard(PROCESSING_KEY), json.loads(raw) if raw else None)
