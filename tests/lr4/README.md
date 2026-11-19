# ЛР №4: прогон тестов

Все команды — в PowerShell из корня репозитория. В `.env` должна быть раскомментирована строка
`COMPOSE_FILE=docker-compose.yml;docker-compose.gpu.yml`, чтобы `docker compose` работал в GPU-режиме.
Результаты складываются в `results/` (папка в `.gitignore`).

```powershell
pip install -r tests/requirements.txt -r loadtest/requirements.txt
```

## 1. Чистый старт и NFR-013 (TC-NFR-013-01, три раза)

```powershell
docker compose down -v
$t = Get-Date; docker compose up -d
do { Start-Sleep 2; try { $h = Invoke-RestMethod http://localhost:8000/api/health } catch { $h = $null } } until ($h.status -eq "ready")
"Готов через {0:N0} с" -f ((Get-Date) - $t).TotalSeconds
docker compose exec worker nvidia-smi --query-gpu=name,memory.used --format=csv
```

## 2. Системные тесты (34 проверки, ~10–15 мин на GPU)

```powershell
pytest tests/lr4 --portrait D:\datasets\set_c\portraits\White_Male_01.png --junitxml results/lr4_system.xml
```
Сводка по тест-кейсам — `results/lr4_results.csv` (Pass/Fail/Skip и пояснение). Ожидаемые Fail
по журналу решений: TC-SEC-01-02 (нет лимита по IP).

## 3. Отказоустойчивость (TC-FR-009-02)

```powershell
pytest tests/lr4 -m ops
```
Ожидается Fail, пока Redis хранит задачи только в памяти (решение 7). После исправления — перезапустить.

## 4. Автоудаление (TC-FR-010-01)

```powershell
$env:RETENTION_SECONDS=120; $env:CLEANUP_INTERVAL_SECONDS=30; docker compose up -d
pytest tests/lr4 -m retention
Remove-Item Env:RETENTION_SECONDS, Env:CLEANUP_INTERVAL_SECONDS; docker compose up -d
```

## 5. Локальная обработка (TC-CON-005-01)

```powershell
$env:COMPOSE_FILE="docker-compose.yml;docker-compose.gpu.yml;docker-compose.offline.yml"; docker compose up -d
pytest tests/lr4 -m offline
Remove-Item Env:COMPOSE_FILE; docker compose up -d
```

## 6. Производительность (TC-NFR-001-01, TC-NFR-001-02, TC-NFR-003-01)

Перед каждым замером: `docker compose down -v; docker compose up -d` — чтобы в /api/metrics были только эти задачи.

```powershell
python tests/lr4/make_load_image.py results/load_2000.jpg
$env:LOAD_IMAGE="results/load_2000.jpg"; $env:LOAD_SCALE="4"

# NFR-001 (GPU): 5 пользователей держат очередь занятой; остановить, когда обработок ≥100
locust -f loadtest/locustfile.py --host http://localhost:8000 --headless -u 5 -r 5 -t 25m --csv results/nfr001_gpu
# в соседнем окне — пик памяти воркера (журнал решений, запись 9):
docker stats --no-stream upscaler-worker-1
Invoke-RestMethod http://localhost:8000/api/metrics | ConvertTo-Json -Depth 5 > results/nfr001_gpu_metrics.json

# NFR-003: одиночный запуск (эталон времени), затем 20 одновременных пользователей
locust -f loadtest/locustfile.py --host http://localhost:8000 --headless -u 1 -r 1 -t 3m --csv results/nfr003_single
locust -f loadtest/locustfile.py --host http://localhost:8000 --headless -u 20 -r 20 -t 15m --csv results/nfr003_20users

# NFR-001 (CPU, справочно): $env:DEVICE="cpu"; docker compose up -d; 10 обработок
locust -f loadtest/locustfile.py --host http://localhost:8000 --headless -u 1 -r 1 -t 30m --csv results/nfr001_cpu
```
p95 обработки — строка `processing` в `*_stats.csv` и `processing_time` в /api/metrics.

## 7. ML-тесты (TC-ML-01, TC-ML-02, TC-FR-006-01, TC-AI-01-*, TC-AI-03-*)

```powershell
$run = "docker compose run --rm -v D:/datasets:/eval -v ./results:/results -v ./worker/tools:/app/tools worker"

# Набор A и B (NFR-004–006, AI-02)
iex "$run python -m tools.eval_metrics ref --hr-dir /eval/DIV2K_valid_HR --limit 100 --out /results/set_a.csv"
iex "$run python -m tools.eval_metrics ref --hr-dir /eval/DIV2K_valid_HR --limit 100 --degrade --out /results/set_b.csv"

# Набор C: FR-006 и fairness, затем дрейф
iex "$run python -m tools.lr4_ml fairness --dir /eval/set_c/portraits --out /results/fairness.csv"
iex "$run python -m tools.lr4_ml drift --dir /eval/set_c/portraits --out /results/drift.csv"
```
Поправьте `D:/datasets` и имя папки DIV2K под свои пути. Вывод консоли сохраните целиком
(`... | Tee-Object results/<имя>.txt`): итоговые строки Pass/Fail нужны для отчёта.

## 8. Ручные проверки

| Тест-кейс | Что сделать |
|---|---|
| TC-FR-004-01 | Обработать фото в браузере: прогресс, слайдер «до/после», карта изменений, уведомление об ограничениях. Скриншот |
| TC-NFR-009-01 | DevTools → ширина 360, 768, 1920 px: нет горизонтальной прокрутки. Три скриншота |
| TC-NFR-008-02 | `python tests/public_mode_test.py --domain <домен> --key <ключ>` с ноутбука через мобильный интернет |
| TC-CON-001-01 | Сверить docs/LICENSES.md: Real-ESRGAN, YuNet, BRISQUE, SwinIR, DIV2K, FairFace |
| TC-CON-002-01 | Просмотреть docker-compose*.yml и requirements*.txt: нет платных сервисов |
| TC-NFR-011-01 | Пилот: ≥5 человек, ≥20 обработок, затем `download_rate` в /api/metrics |
