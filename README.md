# watsonx AI Translator Agent

Japanese PDF → English PDF translation using IBM watsonx.ai foundation models.

## Quick Start

**Prerequisites:** `ibmcloud` CLI, `jq`, Python 3.13+

```bash
# 1. Install dependencies and fetch all credentials → writes .env
./setup-env.sh

# 2. Run locally
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 3. Open Swagger docs
open http://localhost:8000/docs
```

## Deploy to IBM Code Engine

```bash
# Deploys (or updates) the app and automatically sets APP_URL
./deploy.sh
```

`deploy.sh` reads credentials from `.env` (written by `setup-env.sh`), creates or updates
the Code Engine app, and sets `APP_URL` so the OpenAPI spec reflects the real server URL.

After deploy, the OpenAPI spec is ready to import:

```bash
# Download the spec (OpenAPI 3.0.3)
curl -s https://<your-app-url>/openapi.json | jq '.' > openapi-spec.json
```

> Override the app name or project without editing the script:
> ```bash
> APP_NAME=my-app CE_PROJECT=my-project ./deploy.sh
> ```

## Import into watsonx Assistant

1. Go to **Integrations → Extensions → Build custom extension**
2. Upload `openapi-spec.json`
3. Follow the prompts to authenticate and enable the extension

## Environment Variables

| Variable                | Required | Description                                        |
|-------------------------|----------|----------------------------------------------------|
| `IBM_CLOUD_API_KEY`     | Yes      | IBM Cloud API key used to obtain an IAM token      |
| `WATSONX_PROJECT_ID`    | Yes      | watsonx.ai project ID                              |
| `APP_URL`               | Yes      | Deployed app URL — set automatically by `deploy.sh` |
| `WATSONX_URL`           | No       | Region endpoint (default: `https://us-south.ml.cloud.ibm.com`) |
| `WATSONX_API_VERSION`   | No       | API version string (default: `2024-05-01`)         |
| `CHUNK_SIZE`            | No       | Max chars per translation chunk (default: `3000`)  |
| `OUTPUT_COS_ENDPOINT`   | No       | COS endpoint for translated PDF output             |
| `OUTPUT_COS_BUCKET`     | No       | Bucket name for translated PDF output              |
| `OUTPUT_COS_ACCESS_KEY` | No       | HMAC access key for the output bucket              |
| `OUTPUT_COS_SECRET_KEY` | No       | HMAC secret key for the output bucket              |

## API Endpoints

| Method | Path                             | Description                                          |
|--------|----------------------------------|------------------------------------------------------|
| POST   | `/api/v1/translate/pdf`          | Upload a Japanese PDF directly (multipart)           |
| POST   | `/api/v1/translate/from-source`  | Translate from a URL, server file path, or bucket    |
| POST   | `/api/v1/translate/text`         | Translate raw Japanese text                          |
| GET    | `/api/v1/download/{filename}`    | Download a translated PDF                            |
| GET    | `/api/v1/models`                 | List supported models                                |
| GET    | `/api/v1/regions`                | List available regions                               |
| GET    | `/health`                        | Health check                                         |

## cURL Examples

### Translate a PDF (default model: Granite 3 8B)
```bash
curl -X POST "http://localhost:8000/api/v1/translate/pdf" \
  -F "file=@document_ja.pdf" \
  | jq .

# With model selection:
curl -X POST "http://localhost:8000/api/v1/translate/pdf?model_id=meta-llama/llama-3-1-70b-instruct" \
  -F "file=@document_ja.pdf" \
  | jq .

# With region override:
curl -X POST "http://localhost:8000/api/v1/translate/pdf?model_id=mistralai/mistral-large&region=https://eu-de.ml.cloud.ibm.com" \
  -F "file=@document_ja.pdf" \
  | jq .
```

### Translate from a URL (watsonx Assistant file upload)
```bash
curl -X POST "http://localhost:8000/api/v1/translate/from-source" \
  -H "Content-Type: application/json" \
  -d '{
    "source": {
      "type": "url",
      "url": "https://example.com/documents/japanese.pdf"
    },
    "model_id": "ibm/granite-3-8b-instruct"
  }' | jq .
```

> **Tip:** When using watsonx Assistant, the Assistant provides a temporary download URL when a
> user uploads a file in chat. Pass that URL as `source.url`. Use the optional `headers` field
> to include auth headers if the URL requires authentication.

### Translate from a server file path
```bash
curl -X POST "http://localhost:8000/api/v1/translate/from-source" \
  -H "Content-Type: application/json" \
  -d '{
    "source": {
      "type": "file_path",
      "path": "/data/documents/japanese.pdf"
    },
    "model_id": "ibm/granite-3-8b-instruct"
  }' | jq .
```

### Translate from an IBM COS bucket
```bash
curl -X POST "http://localhost:8000/api/v1/translate/from-source" \
  -H "Content-Type: application/json" \
  -d '{
    "source": {
      "type": "bucket",
      "endpoint_url": "https://s3.us-south.cloud-object-storage.appdomain.cloud",
      "bucket": "my-bucket",
      "key": "documents/japanese.pdf",
      "access_key_id": "YOUR_HMAC_ACCESS_KEY",
      "secret_access_key": "YOUR_HMAC_SECRET_KEY"
    },
    "model_id": "ibm/granite-3-8b-instruct",
    "project_id": "optional-project-id-override"
  }' | jq .
```

> **Tip:** `access_key_id` and `secret_access_key` are optional when `OUTPUT_COS_ACCESS_KEY` /
> `OUTPUT_COS_SECRET_KEY` env vars are set.

### Download the translated PDF
```bash
curl -O "http://localhost:8000/api/v1/download/translated_xxxxx.pdf"
```

### Translate raw text (no PDF)
```bash
curl -X POST "http://localhost:8000/api/v1/translate/text" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "人工知能は急速に進化しています。",
    "model_id": "ibm/granite-3-8b-instruct"
  }'
```

### List available models
```bash
curl http://localhost:8000/api/v1/models | jq .
```

## Supported Models

| Provider | Model                              | Best For                        |
|----------|------------------------------------|---------------------------------|
| IBM      | granite-3-8b-instruct              | General translation (default)   |
| IBM      | granite-20b-multilingual           | Multilingual-optimized          |
| Meta     | llama-3-1-70b-instruct             | Highest quality, slower         |
| Meta     | llama-3-1-8b-instruct              | Fast, good quality              |
| Mistral  | mistral-large                      | Strong multilingual             |
| Mistral  | mixtral-8x7b-instruct-v01          | MoE, good balance              |
| ELYZA    | elyza-japanese-llama-2-7b-instruct | Japanese-specialized            |

## Docker Deployment

```bash
docker build -t watsonx-translator .
docker run -p 8000:8000 \
  -e IBM_CLOUD_API_KEY="your-key" \
  -e WATSONX_PROJECT_ID="your-project-id" \
  watsonx-translator
```

## Output Bucket (IBM COS)

When running on Code Engine, translated PDFs are saved to IBM COS so `download_url` returns
a stable, persistent link. `setup-env.sh` creates the HMAC credentials and `deploy.sh` sets
all required env vars automatically.

To make translated PDFs publicly accessible, enable public access on the bucket:
IBM Cloud → Object Storage → your bucket → Access policies → Public access → Enable.

When configured, `download_url` in the response will point directly to the COS object:
```
https://s3.us-south.cloud-object-storage.appdomain.cloud/your-bucket/translated/translated_XXXX.pdf
```
