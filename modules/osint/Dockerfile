# Lekki kontener tylko dla modułu OSINT (bez stosu ML).
#   docker build -t fakecollector-osint -f modules/osint/Dockerfile modules
#   docker run --rm -p 8010:8010 -v "$PWD/data:/data" fakecollector-osint
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OSINT_CACHE_DIR=/data/osint_cache PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app
COPY osint/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY osint /app/osint
EXPOSE 8010
CMD ["uvicorn", "osint.main:app", "--host", "0.0.0.0", "--port", "8010"]
