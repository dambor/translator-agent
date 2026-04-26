# watsonx AI Translator Agent

Multi-format document translation using IBM watsonx.ai. Supports any language pair and preserves the original file format (DOCX→DOCX, XLSX→XLSX, PPTX→PPTX, PDF→PDF).

**Supported formats:** PDF, Word (DOCX), Excel (XLSX), PowerPoint (PPTX), HTML, Markdown, plain text  
**Language pairs:** Any ↔ Any (Japanese, English, Portuguese, Spanish, French, German, Chinese, Korean, …)

---

## Quick Start (Local)

**Prerequisites:** `ibmcloud` CLI, `jq`, Python 3.11+

```bash
# 1. Install dependencies and fetch credentials → writes .env
./setup-env.sh

# 2. Install Python packages
pip install -r requirements.txt

# 3. Run locally
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 4. Open Swagger docs
open http://localhost:8000/docs
```

---

## Deploy to IBM Code Engine

### First deploy

```bash
./deploy.sh
```

This logs you into IBM Cloud, selects a Code Engine project, builds the container image, deploys the app, and saves `APP_URL` to `.env`.

### Subsequent deploys (code changes only)

To avoid rebuilding the full image on every deploy, split the build into a heavy **base image** (built once) and a lightweight **app image** (rebuilt in ~30s):

```bash
# Step 1 — build the base image ONCE (or when requirements.txt changes, ~5 min)
./build-base.sh

# Step 2 — fast redeploy for code-only changes (~30s)
./deploy.sh redeploy
```

`build-base.sh` builds all system libraries, Python packages, and fonts into a base image in IBM Container Registry. `deploy.sh redeploy` only rebuilds the thin layer that copies `main.py`.

### Regenerate the OpenAPI spec

```bash
./deploy.sh openapi
```

Fetches the live spec from the deployed app and saves it as `openapi-spec.json`.

---

## watsonx Orchestrate Integration

Use `openapi-spec-fixed.json` to import the skill into Orchestrate (not `openapi-spec.json`).

### Import the skill

1. In Orchestrate, go to **Skills → Add skill → From file**
2. Upload `openapi-spec-fixed.json`
3. Enable the skill

### Agent prompt

```
You are a document translation assistant powered by IBM watsonx.ai.

When the user asks to translate a document:
- Call the Translate Document skill immediately when a file is attached
- Set source_lang to the language the user mentioned, or "auto" if not specified
- Set target_lang to the language the user wants, default to "English" if not specified

After the skill responds, tell the user:
- The translation is complete
- The output filename
- The download link from the download_url field in the response

If no file is attached, ask the user to attach the document they want to translate.

Supported formats: PDF, Word (DOCX), Excel (XLSX), PowerPoint (PPTX), HTML, Markdown, plain text.
```

### Skill input mapping

- `file` — the user's file attachment (Orchestrate passes it automatically as binary)
- `source_lang` — source language, or `auto` to detect automatically
- `target_lang` — target language (default: `English`)

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/translate/document` | Translate any document (multipart form) |
| POST | `/api/v1/translate/document-base64` | Translate via base64 JSON body |
| POST | `/api/v1/translate/text` | Translate raw text |
| POST | `/api/v1/translate/from-source` | Translate from URL, file path, or COS bucket |
| GET | `/api/v1/download/{filename}` | Download a translated file |
| GET | `/api/v1/health` | Health check |
| GET | `/api/v1/models` | List supported models |
| GET | `/api/v1/formats` | List supported file formats |
| GET | `/api/v1/regions` | List available regions |

---

## cURL Examples

### Translate a document (any format)
```bash
BASE=http://localhost:8000

curl -X POST "$BASE/api/v1/translate/document?source_lang=Japanese&target_lang=English" \
  -F "file=@report.pdf" | jq .

# Portuguese to Spanish
curl -X POST "$BASE/api/v1/translate/document?source_lang=Portuguese&target_lang=Spanish" \
  -F "file=@document.docx" | jq .

# Auto-detect source language
curl -X POST "$BASE/api/v1/translate/document?target_lang=English" \
  -F "file=@unknown.pdf" | jq .
```

### Translate raw text
```bash
curl -X POST "$BASE/api/v1/translate/text" \
  -H "Content-Type: application/json" \
  -d '{"text": "人工知能は急速に進化しています。", "source_lang": "Japanese", "target_lang": "English"}'
```

### Translate from a URL
```bash
curl -X POST "$BASE/api/v1/translate/from-source" \
  -H "Content-Type: application/json" \
  -d '{
    "source": {"type": "url", "url": "https://example.com/document.pdf"},
    "source_lang": "Japanese",
    "target_lang": "English"
  }' | jq .
```

### List available models
```bash
curl "$BASE/api/v1/models" | jq .
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `IBM_CLOUD_API_KEY` | Yes | IBM Cloud API key for IAM token generation |
| `WATSONX_PROJECT_ID` | Yes | watsonx.ai project ID |
| `APP_URL` | Yes | Deployed app URL — set automatically by `deploy.sh` |
| `WATSONX_URL` | No | Region endpoint (default: `https://us-south.ml.cloud.ibm.com`) |
| `OUTPUT_COS_ENDPOINT` | No | COS endpoint for translated file storage |
| `OUTPUT_COS_BUCKET` | No | COS bucket name |
| `OUTPUT_COS_ACCESS_KEY` | No | COS HMAC access key |
| `OUTPUT_COS_SECRET_KEY` | No | COS HMAC secret key |
| `ICR_NAMESPACE` | No | IBM Container Registry namespace for base image (default: `ce--8ff6f-2907fwm9n6us`) |

---

## Supported Models

| Provider | Model ID | Notes |
|----------|----------|-------|
| IBM | `ibm/granite-3-8b-instruct` | Default, fast |
| IBM | `ibm/granite-20b-multilingual` | Multilingual-optimized |
| Meta | `meta-llama/llama-3-1-70b-instruct` | Highest quality |
| Meta | `meta-llama/llama-3-1-8b-instruct` | Fast, good quality |
| Mistral | `mistralai/mistral-large` | Strong multilingual |
| Mistral | `mistralai/mixtral-8x7b-instruct-v01` | Balanced |

---

## Output Files

Translated files are named `{original-name}_translated_{YYYYMMDD_HHMMSS}.{ext}` — for example, `report_translated_20260426_143022.pdf`.

When COS is configured, `download_url` in the response points to a persistent COS object. Without COS, files are served from the app's temp directory via `/api/v1/download/{filename}`.
