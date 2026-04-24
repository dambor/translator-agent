FROM python:3.11-slim

# System libraries required by docling dependencies (onnxruntime, opencv, torch)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    libglib2.0-0 \
    libgl1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies (longer timeout for large packages like docling)
COPY requirements.txt .
RUN pip install --no-cache-dir --timeout=600 -r requirements.txt

# Copy application
COPY main.py .
COPY .env* ./

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -sf http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]