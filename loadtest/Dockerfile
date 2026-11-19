# Locust в контейнере: на Windows с Smart App Control скомпилированные модули gevent
# (зависимость Locust) блокируются политикой управления приложениями
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install "locust==2.32.4" "pillow==11.0.0"

WORKDIR /work
ENTRYPOINT ["locust", "-f", "loadtest/locustfile.py", "--host", "http://api:8000"]
