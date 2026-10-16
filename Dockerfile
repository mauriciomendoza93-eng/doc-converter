# Imagen para el servidor propio (HP): conversor + herramientas PDF.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui \
        fonts-dejavu fonts-liberation fonts-crosextra-carlito \
        ocrmypdf tesseract-ocr-spa tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-server.txt ./
RUN pip install --no-cache-dir -r requirements-server.txt

COPY backend ./backend
COPY frontend ./frontend

RUN useradd --uid 1000 --create-home app && mkdir -p /data && chown app /data
USER app
ENV DATA_DIR=/data HOME=/home/app PYTHONUNBUFFERED=1
EXPOSE 8300
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8300/api/status')"
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8300", "--workers", "2"]
