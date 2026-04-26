FROM private.us.icr.io/ce--8ff6f-2907fwm9n6us/watsonx-translator-base:latest

COPY main.py .
COPY .env* ./

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -sf http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
