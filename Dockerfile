FROM python:3.11-slim

# System libraries:
#   tesseract-ocr      — OCR engine for scanned PDFs
#   tesseract-ocr-*    — language packs
#   poppler-utils      — pdf2image needs pdftoppm
#   fonts-noto-cjk     — CJK font source (TTC collection)
#   libgomp1 libglib2.0-0 libgl1 — image processing libs
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
    fonts-noto-cjk \
    fonts-dejavu-core \
    libgomp1 \
    libglib2.0-0 \
    libgl1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --timeout=300 -r requirements.txt

# Extract individual TTF subfonts from the NotoSansCJK TTC collection.
# fpdf2 renders glyph outlines correctly from TTF/OTF but not from TTC —
# using TTC causes CJK characters to be invisible in the output PDF.
RUN python3 - <<'EOF'
import os, sys
try:
    from fontTools.ttLib import TTCollection
    src = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    out_dir = "/usr/local/share/fonts/noto-cjk"
    os.makedirs(out_dir, exist_ok=True)
    if not os.path.exists(src):
        print(f"WARNING: {src} not found", file=sys.stderr)
        sys.exit(0)
    coll = TTCollection(src)
    for i, name in enumerate(["NotoSansCJKjp", "NotoSansCJKkr", "NotoSansCJKsc", "NotoSansCJKtc"]):
        if i < len(coll):
            out = f"{out_dir}/{name}-Regular.ttf"
            coll[i].save(out)
            print(f"Extracted: {out}")
except Exception as e:
    print(f"Font extraction failed: {e}", file=sys.stderr)
EOF

COPY main.py .
COPY .env* ./

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -sf http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
