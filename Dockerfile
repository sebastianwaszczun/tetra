FROM python:3.11-slim

# CPU domyślnie. Dla GPU zob. docker-compose.gpu.yml (podmienia TORCH_INDEX).
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_EXTRA_INDEX_URL=${TORCH_INDEX} \
    HF_HOME=/models/hf \
    TORCH_HOME=/models/torch \
    INSIGHTFACE_HOME=/models/insightface \
    FACETORCH_HOME=/models/facetorch \
    SPEECHBRAIN_HOME=/models/speechbrain \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Systemowe zależności:
#  - build-essential/cmake: kompilacja insightface
#  - libgl1/libglib2.0-0: OpenCV
#  - ffmpeg/libsndfile1: wideo i audio (klatki, ścieżka głosu dla SpeechBrain)
#  - tesseract-ocr + pol/eng: OCR tekstu z reklam
#  - whois/dnsutils: infrastruktura (WHOIS, dig)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git curl wget ca-certificates \
        libgl1 libglib2.0-0 ffmpeg libsndfile1 \
        tesseract-ocr tesseract-ocr-pol tesseract-ocr-eng \
        whois dnsutils \
    && rm -rf /var/lib/apt/lists/*

# PyTorch najpierw, z właściwego indeksu (CPU/GPU), żeby reszta go nie podmieniła
RUN pip install torch torchvision torchaudio --index-url ${TORCH_INDEX}

# numpy<2 i Cython przed insightface (kompiluje się ze źródeł)
RUN pip install "numpy<2" Cython

COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

# Przeglądarka headless do śledzenia przekierowań JS, zrzutów ekranu i HAR
RUN playwright install --with-deps chromium

WORKDIR /workspace
COPY scripts /workspace/scripts
COPY src /workspace/src

EXPOSE 8888
CMD ["sh", "-c", "jupyter lab --ip=0.0.0.0 --port=8888 --no-browser --allow-root --ServerApp.token=${JUPYTER_TOKEN:-fake} --notebook-dir=/workspace"]
