FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OCR_ENABLED=true \
    OCR_LANGUAGE=spa+eng \
    OCR_DPI=180 \
    OCR_MIN_CHARS=80 \
    OCR_MAX_PAGES_REQUEST=160 \
    MAX_PDF_BYTES=83886080 \
    TESSDATA_PREFIX=/opt/tessdata

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       tesseract-ocr \
       tesseract-ocr-spa \
       tesseract-ocr-eng \
    && TESSDATA_DIR="$(find /usr/share -type d -name tessdata | head -n 1)" \
    && test -n "$TESSDATA_DIR" \
    && test -f "$TESSDATA_DIR/spa.traineddata" \
    && test -f "$TESSDATA_DIR/eng.traineddata" \
    && ln -s "$TESSDATA_DIR" /opt/tessdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

CMD ["gunicorn", "--bind", "0.0.0.0:10000", "app:app"]
