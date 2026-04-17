# watsonx AI Translator Agent

Japanese PDF → English PDF translation using IBM watsonx.ai foundation models.

## Quick Start

```bash
# 1. Install dependencies  (Python 3.13 recommended; 3.14 also works)
pip install -r requirements.txt

# 2. Configure credentials (automated)
./setup-env.sh
# — or manually —
cp .env.example .env
# Edit .env with your IBM Cloud API key and watsonx project ID

# 3. Run the server
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 4. Open Swagger docs
open http://localhost:8000/docs
```

### Environment variables

| Variable                | Required | Description                                        |
|-------------------------|----------|----------------------------------------------------|
| `IBM_CLOUD_API_KEY`     | Yes      | IBM Cloud API key used to obtain an IAM token      |
| `WATSONX_PROJECT_ID`    | Yes      | watsonx.ai project ID                              |
| `WATSONX_URL`           | No       | Region endpoint (default: `https://us-south.ml.cloud.ibm.com`) |
| `WATSONX_API_VERSION`   | No       | API version string (default: `2024-05-01`)         |
| `CHUNK_SIZE`            | No       | Max chars per translation chunk (default: `3000`)  |
| `AWS_ACCESS_KEY_ID`     | No       | IBM COS / S3 HMAC access key (used by `/from-source` bucket source) |
| `AWS_SECRET_ACCESS_KEY` | No       | IBM COS / S3 HMAC secret key (used by `/from-source` bucket source) |
| `OUTPUT_COS_ENDPOINT`   | No       | COS endpoint where translated PDFs are uploaded (recommended for Code Engine) |
| `OUTPUT_COS_BUCKET`     | No       | Bucket name for translated PDF output              |
| `OUTPUT_COS_ACCESS_KEY` | No       | HMAC access key for the output bucket (falls back to `AWS_ACCESS_KEY_ID`) |
| `OUTPUT_COS_SECRET_KEY` | No       | HMAC secret key for the output bucket (falls back to `AWS_SECRET_ACCESS_KEY`) |

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

> **Tip:** `access_key_id` and `secret_access_key` are optional when `AWS_ACCESS_KEY_ID` /
> `AWS_SECRET_ACCESS_KEY` env vars are set. The `endpoint_url` field is required for IBM COS
> and any other non-AWS S3-compatible store; omit it for AWS S3.

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

## Docker Deployment

```bash
# Build
docker build -t watsonx-translator .

# Run
docker run -p 8000:8000 \
  -e IBM_CLOUD_API_KEY="your-key" \
  -e WATSONX_PROJECT_ID="your-project-id" \
  watsonx-translator
```

## Credential Setup Script

`setup-env.sh` automates fetching all required credentials from IBM Cloud and writes them to `.env`.

```bash
chmod +x setup-env.sh
./setup-env.sh
```

The script will:
1. Log in to IBM Cloud (SSO)
2. Create a new IBM Cloud API key
3. Fetch your watsonx project ID (prompts to select if multiple exist)
4. Fetch or create HMAC credentials for IBM COS
5. Ask for your COS bucket name
6. Write all values to `.env`

> **Requires:** `ibmcloud` CLI with the Code Engine plugin, and `jq`.

## IBM Code Engine Deployment

```bash
# Login and target
ibmcloud login
ibmcloud target -g Default
ibmcloud ce project select --name my-project

# Find your credentials
ibmcloud iam api-keys                    # list existing API keys
ibmcloud iam api-key-create my-api-key -d "My API key" --output json   # or create one

# Get your watsonx Project ID
curl -X GET "https://api.dataplatform.cloud.ibm.com/v2/projects" \
  -H "Authorization: Bearer $(ibmcloud iam oauth-tokens --output json | jq -r '.iam_token' | cut -d' ' -f2)"
# Look for "guid" or "id" in the response — that's your watsonx_project_id

# Deploy from local source (no Docker build required)
ibmcloud ce app create \
  --name watsonx-translator \
  --build-source . \
  --port 8000 \
  --min-scale 0 \
  --max-scale 3 \
  --env IBM_CLOUD_API_KEY="your-key" \
  --env WATSONX_PROJECT_ID="your-project-id"

# Redeploy after code changes
ibmcloud ce app update --name watsonx-translator --build-source .
```

## Output Bucket Setup (IBM COS)

When running on Code Engine, translated PDFs should be saved to IBM COS instead of the
ephemeral local filesystem. This makes the `download_url` in the response a stable, persistent
link accessible by watsonx Orchestrate or any other client.

### 1. Create HMAC credentials for your COS instance

```bash
ibmcloud resource service-key-create translator-hmac Writer \
  --instance-name CloudObjectStorage \
  --parameters '{"HMAC": true}'
```

The output contains:
```
cos_hmac_keys:
  access_key_id:     <your-access-key-id>
  secret_access_key: <your-secret-access-key>
```

### 2. Configure the output bucket on Code Engine

```bash
ibmcloud ce app update --name watsonx-translator \
  --env OUTPUT_COS_ENDPOINT=https://s3.us-south.cloud-object-storage.appdomain.cloud \
  --env OUTPUT_COS_BUCKET=your-bucket-name \
  --env OUTPUT_COS_ACCESS_KEY=your-access-key-id \
  --env OUTPUT_COS_SECRET_KEY=your-secret-access-key
```

> **Tip:** Use the endpoint that matches your bucket's region. Find the correct endpoint at
> IBM Cloud Console → Object Storage → your bucket → Configuration.

### 3. Ensure the bucket has public read access

Objects are uploaded without a public ACL. To make translated PDFs publicly accessible,
set the bucket policy to **Public** in the IBM Cloud Console:
IBM Cloud → Object Storage → your bucket → Access policies → Public access → Enable.

### 4. Deploy the updated code

```bash
ibmcloud ce app update --name watsonx-translator --build-source .
```

When configured, the `download_url` field in the response will point directly to the COS object:
```
https://s3.us-south.cloud-object-storage.appdomain.cloud/your-bucket/translated/translated_XXXX.pdf
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

## OpenAPI Integration

Use `openapi-spec.json` (OpenAPI 3.0.3, included in the repo) to import into watsonx Assistant
or any API gateway. The live `/openapi.json` endpoint returns OpenAPI 3.1.0 (FastAPI default)
which some tools do not support.

To regenerate `openapi-spec.json` from a running server and keep it up to date:

```bash
curl -s http://localhost:8000/openapi.json | jq '.' > openapi-spec.json
```

> **Note:** After regenerating, manually update the `"openapi"` version field from `3.1.0`
> to `3.0.3` and add the `servers` block (see section below) before importing.

## Configuring the OpenAPI Spec (`openapi-spec.json`)

The repository includes a pre-built spec file at `openapi-spec.json` ready to import into
watsonx Assistant or any OpenAPI-compatible tool. Before importing, update the `servers`
field with your deployed app URL.

### 1. Get your Code Engine app URL

```bash
ibmcloud ce app get --name watsonx-translator --output json | jq -r '.status.url'
```

### 2. Update the server URL in `openapi-spec.json`

Open `openapi-spec.json` and replace the `servers` block near the bottom of the file:

```json
"servers": [
  {
    "url": "https://<your-app>.<region>.codeengine.appdomain.cloud"
  }
]
```

### 3. Import into watsonx Assistant

1. In watsonx Assistant, go to **Integrations → Extensions → Build custom extension**
2. Upload `openapi-spec.json`
3. Follow the prompts to authenticate with your API key and enable the extension