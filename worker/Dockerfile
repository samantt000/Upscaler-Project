# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

# cpu — сборка без CUDA (~1 ГБ); для GPU docker-compose.gpu.yml передаёт индекс cu124
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ARG TORCH_VERSION=2.5.1
# true — дополнительно ставит torchvision и lpips для скрипта оценки качества
ARG WITH_EVAL=false

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install --index-url ${TORCH_INDEX_URL} torch==${TORCH_VERSION}

COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

RUN if [ "$WITH_EVAL" = "true" ]; then \
      pip install --index-url ${TORCH_INDEX_URL} torchvision==0.20.1 && pip install lpips==0.1.4; \
    fi

# Веса моделей (лицензии — docs/LICENSES.md); контрольные суммы фиксируют версии файлов
ADD --checksum=sha256:4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1 \
    https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth /models/
ADD --checksum=sha256:8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4 \
    https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx /models/
ADD --checksum=sha256:4c44c7eec9e5139830c0bb0e4a044c9cd351ba5bbc9e3798beca3243e2782ff4 \
    https://raw.githubusercontent.com/opencv/opencv_contrib/4.x/modules/quality/samples/brisque_model_live.yml /models/
ADD --checksum=sha256:a18427f4f7ad087524bd0d63389649a3899b51ca6d44a4d5f8d8171f1f914a9c \
    https://raw.githubusercontent.com/opencv/opencv_contrib/4.x/modules/quality/samples/brisque_range_live.yml /models/
# SwinIR-M ×4 (classical и real-world GAN): модели для сравнения в tools.eval_metrics
ADD --checksum=sha256:129dc773ba2d4c07f3eb0bb116fbe692011b7cc072d9ca12797cd3748198610a \
    https://github.com/JingyunLiang/SwinIR/releases/download/v0.0/001_classicalSR_DIV2K_s48w8_SwinIR-M_x4.pth /models/
ADD --checksum=sha256:b9afb61e65e04eb7f8aba5095d070bbe9af28df76acd0c9405aeb33b814bcfc6 \
    https://github.com/JingyunLiang/SwinIR/releases/download/v0.0/003_realSR_BSRGAN_DFO_s64w8_SwinIR-M_x4_GAN.pth /models/
# ADD по URL создаёт файлы с правами 600: без этого процесс не от root не прочитает веса
RUN chmod 755 /models && chmod 644 /models/*

WORKDIR /app
COPY app ./app
COPY tools ./tools

# Процесс работает не от root; /data создаётся заранее, чтобы новый том получил этого владельца
RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app:app /data
USER app

CMD ["python", "-m", "app.main"]
