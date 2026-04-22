"""
watsonx AI Translator Agent
============================
Japanese PDF → English PDF translation using IBM watsonx.ai foundation models.
Exposed as an OpenAPI-compatible REST API (FastAPI).

Supports model selection at request time:
  - IBM Granite (multilingual, instruct)
  - Meta Llama 3.x
  - Mistral / Mixtral
  - And any other watsonx.ai-hosted model

Usage:
  pip install -r requirements.txt
  export IBM_CLOUD_API_KEY="your-key"
  export WATSONX_PROJECT_ID="your-project-id"
  uvicorn main:app --host 0.0.0.0 --port 8000

Swagger UI:  http://localhost:8000/docs
OpenAPI JSON: http://localhost:8000/openapi.json
"""

import os
import io
import re
import tempfile
import logging
import unicodedata
from datetime import date
from enum import Enum
from typing import Literal, Optional, Union

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv
import requests

# Load .env file
load_dotenv()
from fastapi import FastAPI, Request, UploadFile, File, HTTPException, Query
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.units import inch

# ── Logging ─────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("watsonx-translator")

# ── Configuration ───────────────────────────────────────────────────

IBM_CLOUD_API_KEY = os.getenv("IBM_CLOUD_API_KEY", "")
WATSONX_PROJECT_ID = os.getenv("WATSONX_PROJECT_ID", "")
WATSONX_API_VERSION = os.getenv("WATSONX_API_VERSION", "2024-05-01")

# Default region; can be overridden per-request
DEFAULT_WATSONX_URL = os.getenv("WATSONX_URL", "https://us-south.ml.cloud.ibm.com")

# Chunk size for splitting long pages (chars). Keeps prompts within context window.
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "3000"))

# Output bucket (IBM COS / S3) — when set, translated PDFs are uploaded here
# and download_url points directly to the object instead of the local endpoint.
OUTPUT_COS_ENDPOINT  = os.getenv("OUTPUT_COS_ENDPOINT", "")   # e.g. https://s3.us-south.cloud-object-storage.appdomain.cloud
OUTPUT_COS_BUCKET    = os.getenv("OUTPUT_COS_BUCKET", "")      # e.g. prudential-langflow
OUTPUT_COS_ACCESS_KEY = os.getenv("OUTPUT_COS_ACCESS_KEY") or os.getenv("AWS_ACCESS_KEY_ID", "")
OUTPUT_COS_SECRET_KEY = os.getenv("OUTPUT_COS_SECRET_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY", "")


# ── Supported Models ────────────────────────────────────────────────

class ModelID(str, Enum):
    """Supported watsonx.ai foundation models for translation."""

    # IBM Granite
    GRANITE_3_8B_INSTRUCT = "ibm/granite-3-8b-instruct"
    GRANITE_3_2B_INSTRUCT = "ibm/granite-3-2b-instruct"
    GRANITE_20B_MULTILINGUAL = "ibm/granite-20b-multilingual"
    GRANITE_13B_INSTRUCT = "ibm/granite-13b-instruct-v2"

    # Meta Llama
    LLAMA_3_1_70B_INSTRUCT = "meta-llama/llama-3-1-70b-instruct"
    LLAMA_3_1_8B_INSTRUCT = "meta-llama/llama-3-1-8b-instruct"
    LLAMA_3_70B_INSTRUCT = "meta-llama/llama-3-70b-instruct"

    # Mistral
    MISTRAL_LARGE = "mistralai/mistral-large"
    MIXTRAL_8X7B_INSTRUCT = "mistralai/mixtral-8x7b-instruct-v01"

    # Others
    FLAN_UL2 = "google/flan-ul2"
    ELYZA_JAPANESE_LLAMA_2_7B = "elyza/elyza-japanese-llama-2-7b-instruct"


class RegionURL(str, Enum):
    """IBM Cloud regions hosting watsonx.ai."""
    US_SOUTH = "https://us-south.ml.cloud.ibm.com"
    EU_DE = "https://eu-de.ml.cloud.ibm.com"
    EU_GB = "https://eu-gb.ml.cloud.ibm.com"
    JP_TOK = "https://jp-tok.ml.cloud.ibm.com"


# ── Prompt Templates (per model family) ─────────────────────────────

PROMPT_TEMPLATES = {
    "granite": (
        "<|system|>\n"
        "You are an expert Japanese-to-English translator. Translate the following "
        "Japanese text into natural, fluent English. Preserve paragraph structure, "
        "formatting, and technical terminology. Output ONLY the English translation.\n"
        "<|user|>\n{text}\n<|assistant|>\n"
    ),
    "llama": (
        "<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n\n"
        "You are an expert Japanese-to-English translator. Translate the following "
        "Japanese text into natural, fluent English. Preserve paragraph structure, "
        "formatting, and technical terminology. Output ONLY the English translation."
        "<|eot_id|><|start_header_id|>user<|end_header_id|>\n\n"
        "{text}<|eot_id|><|start_header_id|>assistant<|end_header_id|>\n\n"
    ),
    "mistral": (
        "[INST] You are an expert Japanese-to-English translator. Translate the "
        "following Japanese text into natural, fluent English. Preserve paragraph "
        "structure, formatting, and technical terminology. Output ONLY the English "
        "translation.\n\n{text} [/INST]"
    ),
    "generic": (
        "Translate the following Japanese text to English. Preserve formatting and "
        "paragraph structure. Output only the English translation.\n\n"
        "Japanese:\n{text}\n\nEnglish:\n"
    ),
}


def _get_prompt_template(model_id: str) -> str:
    """Select the correct prompt template based on model family."""
    model_lower = model_id.lower()
    if "granite" in model_lower:
        return PROMPT_TEMPLATES["granite"]
    elif "llama" in model_lower:
        return PROMPT_TEMPLATES["llama"]
    elif "mistral" in model_lower or "mixtral" in model_lower:
        return PROMPT_TEMPLATES["mistral"]
    return PROMPT_TEMPLATES["generic"]


# ── Response Schemas ────────────────────────────────────────────────

class ModelInfo(BaseModel):
    id: str
    name: str
    family: str

class ModelsResponse(BaseModel):
    models: list[ModelInfo]

class TranslationPageDetail(BaseModel):
    page: int
    source_chars: int
    translated_chars: int

class TranslateResponse(BaseModel):
    message: str
    pages_translated: int
    model_used: str
    region: str
    download_url: str
    pages: list[TranslationPageDetail]

class TranslateTextRequest(BaseModel):
    """Request body for direct text translation (non-PDF)."""
    text: str = Field(..., description="Japanese text to translate")
    model_id: Optional[str] = Field(default=None, description="watsonx model ID")
    region: Optional[str] = Field(default=None, description="watsonx region URL")
    filename: Optional[str] = Field(default=None, description="Original filename (without extension). Used to name the output PDF, e.g. 'japanese-doc'.")

class TranslateTextResponse(BaseModel):
    translated_text: str
    model_used: str
    source_chars: int
    translated_chars: int
    download_url: Optional[str] = Field(
        default=None,
        description="URL to download the translated content as a PDF (when the input text is long enough to warrant a PDF)",
    )

class HealthResponse(BaseModel):
    status: str
    watsonx_url: str
    project_configured: bool


# ── Upload-source schemas ────────────────────────────────────────────

class FilePathSource(BaseModel):
    """Read the PDF from a path accessible to the server."""
    type: Literal["file_path"]
    path: str = Field(..., description="Absolute or relative path to the PDF on the server filesystem")


class URLSource(BaseModel):
    """Download the PDF from any HTTP/HTTPS URL (e.g. a watsonx Assistant file attachment URL)."""
    type: Literal["url"]
    url: str = Field(..., description="Publicly accessible or pre-signed URL pointing to the PDF")
    headers: Optional[dict] = Field(
        default=None,
        description="Optional HTTP headers (e.g. Authorization) needed to download the file",
    )


class BucketSource(BaseModel):
    """Read the PDF from an S3-compatible bucket (IBM COS, AWS S3, MinIO, …)."""
    type: Literal["bucket"]
    endpoint_url: Optional[str] = Field(
        default=None,
        description=(
            "S3-compatible endpoint URL. Required for IBM COS "
            "(e.g. https://s3.us-south.cloud-object-storage.appdomain.cloud). "
            "Leave empty for AWS S3."
        ),
    )
    bucket: str = Field(..., description="Bucket name")
    key: str = Field(..., description="Object key / path inside the bucket")
    access_key_id: Optional[str] = Field(
        default=None,
        description="AWS_ACCESS_KEY_ID / IBM HMAC access key. Falls back to env var.",
    )
    secret_access_key: Optional[str] = Field(
        default=None,
        description="AWS_SECRET_ACCESS_KEY / IBM HMAC secret key. Falls back to env var.",
    )
    region_name: Optional[str] = Field(
        default=None,
        description="Bucket region (e.g. 'us-south'). Optional for IBM COS.",
    )


class TranslatePdfBase64Request(BaseModel):
    """Translate a PDF supplied as a base64-encoded string — preferred for AI orchestration tools."""
    file: str = Field(
        ...,
        description="Base64-encoded PDF file content",
    )
    filename: Optional[str] = Field(
        default="document.pdf",
        description="Original filename (used to name the output PDF)",
    )
    model_id: Optional[str] = Field(
        default=None,
        description="watsonx.ai model ID (see /api/v1/models). Defaults to granite-3-8b-instruct.",
    )
    region: Optional[str] = Field(
        default=None,
        description="watsonx.ai region URL. Defaults to WATSONX_URL env var.",
    )
    project_id: Optional[str] = Field(
        default=None,
        description="watsonx project ID. Defaults to WATSONX_PROJECT_ID env var.",
    )


class TranslateFromSourceRequest(BaseModel):
    """Translate a PDF referenced by a local path, HTTP URL, or cloud bucket object."""
    source: Union[FilePathSource, URLSource, BucketSource] = Field(
        ...,
        discriminator="type",
        description="PDF source — a server file path, an HTTP/HTTPS URL, or a bucket object reference",
    )
    model_id: Optional[str] = Field(
        default=None,
        description="watsonx.ai model ID (see /api/v1/models). Defaults to granite-3-8b-instruct.",
    )
    region: Optional[str] = Field(
        default=None,
        description="watsonx.ai region URL. Defaults to WATSONX_URL env var.",
    )
    project_id: Optional[str] = Field(
        default=None,
        description="watsonx project ID. Defaults to WATSONX_PROJECT_ID env var.",
    )


# ── Core Services ───────────────────────────────────────────────────

class IAMTokenManager:
    """Manages IBM Cloud IAM token lifecycle with simple caching."""

    def __init__(self):
        self._token: Optional[str] = None

    def get_token(self, api_key: Optional[str] = None) -> str:
        key = api_key or IBM_CLOUD_API_KEY
        if not key:
            raise HTTPException(
                status_code=500,
                detail="IBM_CLOUD_API_KEY not configured. Set it as an env var or pass via header.",
            )
        # In production, cache and refresh based on expiry.
        # For simplicity, we fetch a new token each time.
        logger.info("Requesting IAM token...")
        resp = requests.post(
            "https://iam.cloud.ibm.com/identity/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=f"grant_type=urn:ibm:params:oauth:grant-type:apikey&apikey={key}",
            timeout=30,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"IAM token request failed: {resp.text}")
        self._token = resp.json()["access_token"]
        logger.info("IAM token acquired.")
        return self._token


token_manager = IAMTokenManager()


def extract_text_from_pdf(file_bytes: bytes) -> list[str]:
    """Extract text from each page of a PDF. Returns list of page texts."""
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to read PDF: {e}")

    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text()
        if text and text.strip():
            pages.append(text.strip())
            logger.info(f"Page {i + 1}: extracted {len(text)} chars")
        else:
            logger.warning(f"Page {i + 1}: no extractable text, skipping")
    return pages


def chunk_text(text: str, max_chars: int = CHUNK_SIZE) -> list[str]:
    """Split text into chunks, preferring paragraph boundaries."""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    paragraphs = text.split("\n")
    current_chunk = ""

    for para in paragraphs:
        if len(current_chunk) + len(para) + 1 > max_chars and current_chunk:
            chunks.append(current_chunk.strip())
            current_chunk = para
        else:
            current_chunk += "\n" + para if current_chunk else para

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    # If any chunk is still too large, hard-split
    final_chunks = []
    for chunk in chunks:
        if len(chunk) > max_chars:
            for i in range(0, len(chunk), max_chars):
                final_chunks.append(chunk[i : i + max_chars])
        else:
            final_chunks.append(chunk)

    return final_chunks


def translate_text(
    text: str,
    model_id: str,
    token: str,
    watsonx_url: str,
    project_id: str,
    temperature: float = 0.1,
    max_new_tokens: int = 4096,
) -> str:
    """Call watsonx.ai text generation API to translate a single chunk."""

    prompt_template = _get_prompt_template(model_id)
    prompt = prompt_template.format(text=text)

    payload = {
        "model_id": model_id,
        "input": prompt,
        "project_id": project_id,
        "parameters": {
            "max_new_tokens": max_new_tokens,
            "temperature": temperature,
            "top_p": 0.95,
            "top_k": 50,
            "repetition_penalty": 1.05,
            "stop_sequences": [],
        },
    }

    url = f"{watsonx_url}/ml/v1/text/generation?version={WATSONX_API_VERSION}"
    logger.info(f"Calling watsonx.ai: model={model_id}, chars={len(text)}")

    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json=payload,
        timeout=180,
    )

    if resp.status_code != 200:
        logger.error(f"watsonx API error {resp.status_code}: {resp.text}")
        raise HTTPException(
            status_code=502,
            detail=f"watsonx.ai API error ({resp.status_code}): {resp.text[:500]}",
        )

    results = resp.json().get("results", [])
    if not results:
        raise HTTPException(status_code=502, detail="Empty response from watsonx.ai")

    generated = results[0].get("generated_text", "").strip()
    logger.info(f"Translation received: {len(generated)} chars")
    return generated


def translate_page(
    page_text: str, model_id: str, token: str, watsonx_url: str, project_id: str
) -> str:
    """Translate a full page, chunking if necessary."""
    chunks = chunk_text(page_text)
    translated_chunks = []

    for i, chunk in enumerate(chunks):
        logger.info(f"  Translating chunk {i + 1}/{len(chunks)} ({len(chunk)} chars)")
        translated = translate_text(chunk, model_id, token, watsonx_url, project_id)
        translated_chunks.append(translated)

    return "\n\n".join(translated_chunks)


def _safe_para(text: str) -> str:
    """Sanitize text for ReportLab Paragraph: strip control chars, escape XML entities."""
    # Keep printable chars + normal whitespace (newline, tab, space)
    cleaned = "".join(
        c for c in text
        if c in ("\n", "\t", " ") or not unicodedata.category(c).startswith("C")
    )
    return (
        cleaned
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def create_english_pdf(pages: list[str], output_path: str) -> None:
    """Generate a styled English PDF from translated page texts."""
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Title"],
        fontSize=18,
        leading=22,
        spaceAfter=20,
        textColor="#1a1a2e",
    )
    page_header_style = ParagraphStyle(
        "PageHeader",
        parent=styles["Heading2"],
        fontSize=13,
        leading=18,
        spaceAfter=8,
        spaceBefore=12,
        textColor="#0f3460",
        borderWidth=0,
        borderPadding=0,
    )
    body_style = ParagraphStyle(
        "TranslatedBody",
        parent=styles["BodyText"],
        fontSize=11,
        leading=16,
        spaceAfter=8,
    )
    meta_style = ParagraphStyle(
        "MetaInfo",
        parent=styles["Italic"],
        fontSize=9,
        leading=12,
        textColor="#666666",
        spaceAfter=16,
    )

    story = []
    story.append(Paragraph("Translated Document", title_style))
    story.append(
        Paragraph(
            f"Source: Japanese &rarr; English &nbsp;|&nbsp; Pages: {len(pages)}",
            meta_style,
        )
    )
    story.append(Spacer(1, 0.2 * inch))

    for i, page_text in enumerate(pages):
        if i > 0:
            story.append(PageBreak())
        story.append(Paragraph(f"Page {i + 1}", page_header_style))

        for para in page_text.split("\n\n"):
            cleaned = para.strip()
            if cleaned:
                story.append(Paragraph(_safe_para(cleaned), body_style))

        story.append(Spacer(1, 0.15 * inch))

    doc.build(story)
    logger.info(f"English PDF created: {output_path}")


def upload_to_cos(local_path: str, object_key: str) -> str:
    """Upload a file to the configured output COS bucket. Returns the public object URL."""
    s3 = boto3.client(
        "s3",
        endpoint_url=OUTPUT_COS_ENDPOINT,
        aws_access_key_id=OUTPUT_COS_ACCESS_KEY,
        aws_secret_access_key=OUTPUT_COS_SECRET_KEY,
    )
    with open(local_path, "rb") as fh:
        s3.upload_fileobj(
            fh,
            OUTPUT_COS_BUCKET,
            object_key,
            ExtraArgs={"ContentType": "application/pdf", "ACL": "public-read"},
        )
    logger.info(f"Uploaded translated PDF to bucket {OUTPUT_COS_BUCKET}/{object_key}")
    # Use virtual-hosted style URL (bucket.endpoint/key) for public access
    endpoint_host = OUTPUT_COS_ENDPOINT.replace("https://", "").replace("http://", "")
    return f"https://{OUTPUT_COS_BUCKET}.{endpoint_host}/{object_key}"


def _output_filename(original_name: str) -> str:
    """Build output filename: {stem}_translated_YYYYMMDD.pdf"""
    stem = re.sub(r"[^a-zA-Z0-9_\-]", "_", os.path.splitext(original_name)[0]) if original_name else "document"
    today = date.today().strftime("%Y%m%d")
    return f"{stem}_translated_{today}.pdf"


def finalize_translated_pdf(translated_pages: list[str], original_name: str, base_url: str = "") -> str:
    """Write translated PDF to temp file, optionally upload to COS. Returns absolute download URL."""
    output_filename = _output_filename(original_name)
    output_path = os.path.join(tempfile.gettempdir(), output_filename)
    create_english_pdf(translated_pages, output_path)

    if OUTPUT_COS_ENDPOINT and OUTPUT_COS_BUCKET and OUTPUT_COS_ACCESS_KEY:
        object_key = f"translated/{output_filename}"
        return upload_to_cos(output_path, object_key)

    # Return a fully-qualified URL so Orchestrate / Assistant can present it as a clickable link.
    prefix = base_url.rstrip("/") if base_url else ""
    return f"{prefix}/api/v1/download/{output_filename}"


def load_pdf_bytes_from_source(source: Union[FilePathSource, URLSource, BucketSource]) -> bytes:
    """Return the raw bytes of a PDF from a file-path, URL, or bucket source."""
    if source.type == "file_path":
        path = source.path
        if not os.path.isfile(path):
            raise HTTPException(status_code=404, detail=f"File not found: {path}")
        if not path.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Only PDF files are accepted.")
        with open(path, "rb") as fh:
            data = fh.read()
        if not data.startswith(b"%PDF"):
            raise HTTPException(status_code=400, detail="File does not appear to be a valid PDF.")
        return data

    if source.type == "url":
        logger.info(f"Downloading PDF from URL: {source.url}")
        try:
            resp = requests.get(source.url, headers=source.headers or {}, timeout=60)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"Failed to download file: {exc}")
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"URL returned HTTP {resp.status_code}",
            )
        content_type = resp.headers.get("Content-Type", "")
        if "pdf" not in content_type and not source.url.split("?")[0].lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="URL does not point to a PDF file.")
        if not resp.content.startswith(b"%PDF"):
            raise HTTPException(
                status_code=400,
                detail=f"Downloaded content is not a valid PDF (Content-Type: {content_type}).",
            )
        return resp.content

    # bucket source
    try:
        s3_kwargs: dict = {}
        if source.endpoint_url:
            s3_kwargs["endpoint_url"] = source.endpoint_url
        if source.region_name:
            s3_kwargs["region_name"] = source.region_name
        if source.access_key_id and source.secret_access_key:
            s3_kwargs["aws_access_key_id"] = source.access_key_id
            s3_kwargs["aws_secret_access_key"] = source.secret_access_key

        s3 = boto3.client("s3", **s3_kwargs)
        logger.info(f"Fetching s3://{source.bucket}/{source.key}")
        response = s3.get_object(Bucket=source.bucket, Key=source.key)
        data: bytes = response["Body"].read()
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        raise HTTPException(status_code=404, detail=f"Bucket object not found ({code}): {exc}")
    except BotoCoreError as exc:
        raise HTTPException(status_code=502, detail=f"Bucket access error: {exc}")

    if not source.key.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Object key must point to a .pdf file.")
    return data


# ── FastAPI Application ─────────────────────────────────────────────

app = FastAPI(
    title="watsonx AI Translator Agent",
    description=(
        "Translate Japanese PDFs to English using IBM watsonx.ai foundation models. "
        "Supports model selection at request time (Granite, Llama, Mistral, etc.). "
        "Upload a Japanese PDF and receive a translated English PDF."
    ),
    version="1.0.0",
    contact={"name": "watsonx Translator Agent"},
    license_info={"name": "Apache 2.0"},
)


def _patch_schema(obj):
    if isinstance(obj, dict):
        # anyOf [{type: X}, {type: null}] → type: X + nullable: true
        if "anyOf" in obj:
            non_null = [s for s in obj["anyOf"] if s != {"type": "null"}]
            if len(non_null) < len(obj["anyOf"]):
                obj.pop("anyOf")
                obj.update(non_null[0] if len(non_null) == 1 else {"anyOf": non_null})
                obj["nullable"] = True
        # const → enum (3.0.3 doesn't support const)
        if "const" in obj:
            obj["enum"] = [obj.pop("const")]
        # contentMediaType → format: binary (file uploads)
        if "contentMediaType" in obj:
            obj.pop("contentMediaType")
            obj["format"] = "binary"
        for v in list(obj.values()):
            _patch_schema(v)
    elif isinstance(obj, list):
        for item in obj:
            _patch_schema(item)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        contact=app.contact,
        license_info=app.license_info,
        routes=app.routes,
    )
    schema["openapi"] = "3.0.3"
    _patch_schema(schema)
    schema["servers"] = [{"url": os.getenv("APP_URL", "http://localhost:8000")}]
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi


# ── Endpoints ───────────────────────────────────────────────────────


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    """Check API health and configuration status."""
    return HealthResponse(
        status="ok",
        watsonx_url=DEFAULT_WATSONX_URL,
        project_configured=bool(WATSONX_PROJECT_ID),
    )


@app.get("/api/v1/models", response_model=ModelsResponse, tags=["Models"])
async def list_models():
    """List all supported watsonx.ai models for translation."""
    models = []
    for m in ModelID:
        parts = m.value.split("/")
        family = parts[0] if len(parts) > 1 else "unknown"
        name = parts[1] if len(parts) > 1 else m.value
        models.append(ModelInfo(id=m.value, name=name, family=family))
    return ModelsResponse(models=models)


@app.get("/api/v1/regions", tags=["Configuration"])
async def list_regions():
    """List available IBM Cloud regions for watsonx.ai."""
    return {
        "regions": [{"id": r.name.lower(), "url": r.value} for r in RegionURL]
    }


@app.post(
    "/api/v1/translate/pdf",
    response_model=TranslateResponse,
    tags=["Translation"],
    summary="Translate a Japanese PDF to English",
    description=(
        "Upload a Japanese PDF. The agent extracts text per page, translates each "
        "page using the selected watsonx.ai model, and returns a downloadable English PDF."
    ),
)
async def translate_pdf(
    request: Request,
    file: UploadFile = File(..., description="Japanese PDF file to translate"),
    model_id: str = Query(
        default=ModelID.GRANITE_3_8B_INSTRUCT.value,
        description="watsonx.ai model ID (see /api/v1/models for options)",
    ),
    region: str = Query(
        default=None,
        description="watsonx.ai region URL (defaults to WATSONX_URL env var)",
    ),
    project_id: str = Query(
        default=None,
        description="watsonx project ID (defaults to WATSONX_PROJECT_ID env var)",
    ),
):
    # Validate file
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    watsonx_url = region or DEFAULT_WATSONX_URL
    wx_project_id = project_id or WATSONX_PROJECT_ID
    if not wx_project_id:
        raise HTTPException(
            status_code=400,
            detail="Project ID required. Set WATSONX_PROJECT_ID env var or pass as query param.",
        )

    # 1. Read PDF
    logger.info(f"Received PDF: {file.filename}")
    file_bytes = await file.read()

    # 2. Extract text
    pages = extract_text_from_pdf(file_bytes)
    if not pages:
        raise HTTPException(
            status_code=422,
            detail="No extractable text found in the PDF. Ensure it contains selectable text (not scanned images).",
        )
    logger.info(f"Extracted {len(pages)} pages with text.")

    # 3. Get IAM token
    token = token_manager.get_token()

    # 4. Translate each page
    translated_pages = []
    page_details = []

    for i, page_text in enumerate(pages):
        logger.info(f"Translating page {i + 1}/{len(pages)}...")
        translated = translate_page(page_text, model_id, token, watsonx_url, wx_project_id)
        translated_pages.append(translated)
        page_details.append(
            TranslationPageDetail(
                page=i + 1,
                source_chars=len(page_text),
                translated_chars=len(translated),
            )
        )

    # 5. Generate English PDF (upload to COS if configured, else serve locally)
    download_url = finalize_translated_pdf(translated_pages, file.filename or "document", str(request.base_url))
    logger.info(f"Translation complete: {len(translated_pages)} pages → {download_url}")

    return TranslateResponse(
        message="Translation complete",
        pages_translated=len(translated_pages),
        model_used=model_id,
        region=watsonx_url,
        download_url=download_url,
        pages=page_details,
    )


@app.post(
    "/api/v1/translate/pdf-base64",
    response_model=TranslateResponse,
    tags=["Translation"],
    summary="Translate a Japanese PDF supplied as base64 (JSON body)",
    description=(
        "Send a base64-encoded Japanese PDF in a JSON body. "
        "Intended for AI orchestration tools (e.g. watsonx Orchestrate) that cannot "
        "perform multipart file uploads. The agent decodes the PDF, translates each "
        "page, and returns a downloadable English PDF."
    ),
)
async def translate_pdf_base64(request: Request, body: TranslatePdfBase64Request):
    import base64

    model_id = body.model_id or ModelID.GRANITE_3_8B_INSTRUCT.value
    watsonx_url = body.region or DEFAULT_WATSONX_URL
    wx_project_id = body.project_id or WATSONX_PROJECT_ID

    if not wx_project_id:
        raise HTTPException(
            status_code=400,
            detail="Project ID required. Set WATSONX_PROJECT_ID env var or pass as 'project_id'.",
        )

    # Decode base64 → bytes
    try:
        file_bytes = base64.b64decode(body.file)
    except Exception:
        raise HTTPException(status_code=400, detail="'file' is not valid base64-encoded content.")

    if not file_bytes.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="Decoded content is not a valid PDF.")

    filename = body.filename or "document.pdf"
    logger.info(f"Received base64 PDF: {filename} ({len(file_bytes)} bytes)")

    pages = extract_text_from_pdf(file_bytes)
    if not pages:
        raise HTTPException(
            status_code=422,
            detail="No extractable text found in the PDF. Ensure it contains selectable text (not scanned images).",
        )

    token = token_manager.get_token()
    translated_pages = []
    page_details = []

    for i, page_text in enumerate(pages):
        logger.info(f"Translating page {i + 1}/{len(pages)}...")
        translated = translate_page(page_text, model_id, token, watsonx_url, wx_project_id)
        translated_pages.append(translated)
        page_details.append(
            TranslationPageDetail(
                page=i + 1,
                source_chars=len(page_text),
                translated_chars=len(translated),
            )
        )

    download_url = finalize_translated_pdf(translated_pages, filename, str(request.base_url))
    logger.info(f"Translation complete: {len(translated_pages)} pages → {download_url}")

    return TranslateResponse(
        message="Translation complete",
        pages_translated=len(translated_pages),
        model_used=model_id,
        region=watsonx_url,
        download_url=download_url,
        pages=page_details,
    )


@app.post(
    "/api/v1/translate/from-source",
    response_model=TranslateResponse,
    tags=["Translation"],
    summary="Translate a Japanese PDF referenced by file path or bucket",
    description=(
        "Provide a PDF source — either a server-side file path or an S3-compatible "
        "bucket object (IBM COS, AWS S3, MinIO, …). The agent fetches the PDF, "
        "translates it, and returns a downloadable English PDF."
    ),
)
async def translate_from_source(request: Request, body: TranslateFromSourceRequest):
    model_id = body.model_id or ModelID.GRANITE_3_8B_INSTRUCT.value
    watsonx_url = body.region or DEFAULT_WATSONX_URL
    wx_project_id = body.project_id or WATSONX_PROJECT_ID

    if not wx_project_id:
        raise HTTPException(
            status_code=400,
            detail="Project ID required. Set WATSONX_PROJECT_ID env var or pass as 'project_id'.",
        )

    # 1. Fetch PDF bytes from the requested source
    file_bytes = load_pdf_bytes_from_source(body.source)

    # 2. Extract text
    pages = extract_text_from_pdf(file_bytes)
    if not pages:
        raise HTTPException(
            status_code=422,
            detail="No extractable text found in the PDF. Ensure it contains selectable text.",
        )
    logger.info(f"Extracted {len(pages)} pages from source {body.source.type}.")

    # 3. Get IAM token
    token = token_manager.get_token()

    # 4. Translate each page
    translated_pages = []
    page_details = []

    for i, page_text in enumerate(pages):
        logger.info(f"Translating page {i + 1}/{len(pages)}...")
        translated = translate_page(page_text, model_id, token, watsonx_url, wx_project_id)
        translated_pages.append(translated)
        page_details.append(
            TranslationPageDetail(
                page=i + 1,
                source_chars=len(page_text),
                translated_chars=len(translated),
            )
        )

    # 5. Generate English PDF — derive filename from source
    if body.source.type == "url":
        source_name = body.source.url.split("?")[0].rstrip("/").split("/")[-1] or "document"
    elif body.source.type == "file_path":
        source_name = os.path.basename(body.source.path)
    else:  # bucket
        source_name = body.source.key.split("/")[-1]
    download_url = finalize_translated_pdf(translated_pages, source_name, str(request.base_url))
    logger.info(f"Translation complete: {len(translated_pages)} pages → {download_url}")

    return TranslateResponse(
        message="Translation complete",
        pages_translated=len(translated_pages),
        model_used=model_id,
        region=watsonx_url,
        download_url=download_url,
        pages=page_details,
    )


@app.post(
    "/api/v1/translate/text",
    response_model=TranslateTextResponse,
    tags=["Translation"],
    summary="Translate Japanese text to English (direct)",
    description="Send raw Japanese text and receive the English translation. Useful for testing or non-PDF workflows.",
)
async def translate_text_endpoint(request: Request, body: TranslateTextRequest):
    model_id = body.model_id or ModelID.GRANITE_3_8B_INSTRUCT.value
    watsonx_url = body.region or DEFAULT_WATSONX_URL
    wx_project_id = WATSONX_PROJECT_ID

    if not wx_project_id:
        raise HTTPException(status_code=400, detail="WATSONX_PROJECT_ID env var not set.")

    if not body.text or not body.text.strip():
        raise HTTPException(status_code=400, detail="'text' field is empty. Pass the full Japanese text extracted from the document.")

    token = token_manager.get_token()
    translated = translate_page(body.text, model_id, token, watsonx_url, wx_project_id)

    # Always generate a PDF so the agent can return a stable download link
    download_url = finalize_translated_pdf([translated], body.filename or "document", str(request.base_url))

    return TranslateTextResponse(
        translated_text=translated,
        model_used=model_id,
        source_chars=len(body.text),
        translated_chars=len(translated),
        download_url=download_url,
    )


@app.get(
    "/api/v1/download/{filename}",
    tags=["Download"],
    summary="Download a translated PDF",
    responses={
        200: {"content": {"application/pdf": {}}, "description": "Translated English PDF"},
        404: {"description": "File not found"},
    },
)
async def download_translated_pdf(filename: str):
    """Download a previously translated PDF by filename."""
    # Sanitize filename to prevent path traversal
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "", filename)
    path = os.path.join(tempfile.gettempdir(), safe_name)

    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found or expired.")

    return FileResponse(
        path,
        media_type="application/pdf",
        filename="translated_en.pdf",
        headers={"Content-Disposition": "attachment; filename=translated_en.pdf"},
    )


# ── Entry Point ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")