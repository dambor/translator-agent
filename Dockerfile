FROM python:3.11-slim

# System libraries:
#   tesseract-ocr      — OCR engine for scanned PDFs
#   tesseract-ocr-*    — language packs (add more as needed)
#   poppler-utils      — pdf2image needs pdftoppm from poppler
#   libgomp1           — required by pytesseract/onnxruntime
#   libglib2.0-0 libgl1 — image processing libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-jpn \
    tesseract-ocr-por \
    tesseract-ocr-spa \
    tesseract-ocr-fra \
    tesseract-ocr-deu \
    tesseract-ocr-ita \
    tesseract-ocr-kor \
    tesseract-ocr-chi-sim \
    poppler-utils \
    libgomp1 \
    libglib2.0-0 \
    libgl1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --timeout=300 -r requirements.txt

COPY main.py .
COPY .env* ./

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -sf http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
