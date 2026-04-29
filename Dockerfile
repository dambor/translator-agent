FROM private.us.icr.io/ce--8ff6f-2907fwm9n6us/watsonx-translator-base:latest

# Install reportlab for CJK PDF generation.
# Uses Adobe CID fonts (HeiseiMin-W3, STSong-Light, etc.) — standard PDF fonts
# that don't need any font file. Completely avoids the fpdf2 CFF subsetting bug.
RUN pip install --no-cache-dir 'reportlab>=4.0.0'

COPY main.py .
COPY .env* ./

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -sf http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
