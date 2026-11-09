# Лицензии моделей, данных и зависимостей (CON-001, CON-008)

Таблица составлена по страницам источников. Перед защитой сверьте каждую строку
с первоисточником (ссылки ниже) — это и есть проверка по CON-001 и CON-008.

| Компонент | Назначение | Лицензия | Источник |
|---|---|---|---|
| Real-ESRGAN (код и веса `RealESRGAN_x4plus.pth`) | Суперразрешение (FR-003) | BSD-3-Clause | https://github.com/xinntao/Real-ESRGAN |
| SwinIR-M ×4: classical и real-world GAN (код архитектуры и веса) | Сравнение моделей (tools.eval_metrics); задел для второго режима | Apache-2.0 | https://github.com/JingyunLiang/SwinIR |
| YuNet `face_detection_yunet_2023mar.onnx` | Детекция лиц (FR-006) | MIT | https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet |
| BRISQUE `brisque_model_live.yml`, `brisque_range_live.yml` | Контроль качества (FR-012) | Apache-2.0 (opencv_contrib) | https://github.com/opencv/opencv_contrib/tree/4.x/modules/quality |
| LPIPS | Метрика NFR-006 (только оценка) | BSD-2-Clause | https://github.com/richzhang/PerceptualSimilarity |
| PyTorch | Инференс | BSD-3-Clause | https://github.com/pytorch/pytorch |
| OpenCV (opencv-contrib-python-headless) | Обработка изображений | Apache-2.0 | https://github.com/opencv/opencv-python |
| FastAPI, Uvicorn, redis-py, Pillow | Веб-сервис | MIT / BSD-3 / MIT / MIT-CMU | страницы проектов на PyPI и GitHub |
| Redis 7.2 (`redis:7.2-alpine`) | Очередь задач | BSD-3-Clause (последняя ветка под BSD) | https://github.com/redis/redis/tree/7.2 |
| Caddy 2.10 | Обратный прокси режима демонстрации | Apache-2.0 | https://github.com/caddyserver/caddy |
| caddy-dns/duckdns v0.5.0 | Выпуск сертификата через DNS DuckDNS | В репозитории файла лицензии нет (проверено по архиву модуля v0.5.0); **уточнить у автора или в README** | https://github.com/caddy-dns/duckdns |
| libdns/duckdns v0.3.0 | Клиент API DuckDNS (зависимость модуля) | MIT | https://github.com/libdns/duckdns |
| qrcode | QR-код ссылки-приглашения | BSD-3-Clause | https://github.com/lincolnloop/python-qrcode |
| DuckDNS (сервис) | Домен и запись TXT для сертификата; изображения не получает | Бесплатный сервис | https://www.duckdns.org |
| Let's Encrypt (сервис) | Центр сертификации; изображения не получает | Бесплатный сервис | https://letsencrypt.org |
| DIV2K | Наборы A и B (оценка) | Только для академических исследований | https://data.vision.ee.ethz.ch/cvl/DIV2K/ |
| Flickr2K | Лёгкое дообучение (если будет) | Изображения Flickr; исследовательское использование | https://github.com/limbee/NTIRE2017 |

Замечания:

- Сервис бесплатный и некоммерческий, поэтому ограничение DIV2K «только для исследований»
  не нарушается (CON-008). Изображения DIV2K в репозиторий и образы не включаются.
- Redis закреплён на ветке 7.2: начиная с 7.4 лицензия сменилась на RSALv2/SSPLv1.
- Изображения пользователей для обучения не используются и удаляются через 24 часа (FR-010).
