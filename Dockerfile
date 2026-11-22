FROM python:3.11-slim

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

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git curl wget ca-certificates \
        libgl1 libglib2.0-0 ffmpeg libsndfile1 \
        tesseract-ocr tesseract-ocr-pol tesseract-ocr-eng \
        whois dnsutils \
    && rm -rf /var/lib/apt/lists/*

ENV PIP_NO_CACHE_DIR=false \
    PIP_DEFAULT_TIMEOUT=180 \
    PIP_RETRIES=10

COPY constraints.txt /tmp/constraints.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -c /tmp/constraints.txt torch torchvision torchaudio --index-url ${TORCH_INDEX}

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -c /tmp/constraints.txt "numpy<2" Cython

COPY requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -c /tmp/constraints.txt -r /tmp/requirements.txt

RUN playwright install --with-deps chromium

WORKDIR /workspace
COPY scripts /workspace/scripts
COPY src /workspace/src

EXPOSE 8888
CMD ["sh", "-c", "jupyter lab --ip=0.0.0.0 --port=8888 --no-browser --allow-root --ServerApp.token=${JUPYTER_TOKEN:-fake} --notebook-dir=/workspace"]
