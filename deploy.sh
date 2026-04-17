#!/usr/bin/env bash
# Deploys the watsonx Translator Agent to IBM Code Engine.
# Run setup-env.sh first to populate .env with credentials.
set -euo pipefail

ENV_FILE="$(dirname "$0")/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: .env not found. Run ./setup-env.sh first."
  exit 1
fi

# Load .env
set -a; source "$ENV_FILE"; set +a

APP_NAME="${APP_NAME:-watsonx-translator}"
CE_PROJECT="${CE_PROJECT:-my-project}"

echo "==> Logging in to IBM Cloud..."
ibmcloud login --sso -q
ibmcloud target -g Default
ibmcloud ce project select --name "$CE_PROJECT"

# ── Deploy or update ────────────────────────────────────────────────

if ibmcloud ce app get --name "$APP_NAME" &>/dev/null; then
  echo ""
  echo "==> Updating existing app '$APP_NAME'..."
  ibmcloud ce app update \
    --name "$APP_NAME" \
    --build-source . \
    --env IBM_CLOUD_API_KEY="$IBM_CLOUD_API_KEY" \
    --env WATSONX_PROJECT_ID="$WATSONX_PROJECT_ID" \
    --env WATSONX_URL="${WATSONX_URL:-https://us-south.ml.cloud.ibm.com}" \
    --env OUTPUT_COS_ENDPOINT="${OUTPUT_COS_ENDPOINT:-}" \
    --env OUTPUT_COS_BUCKET="${OUTPUT_COS_BUCKET:-}" \
    --env OUTPUT_COS_ACCESS_KEY="${OUTPUT_COS_ACCESS_KEY:-}" \
    --env OUTPUT_COS_SECRET_KEY="${OUTPUT_COS_SECRET_KEY:-}"
else
  echo ""
  echo "==> Creating app '$APP_NAME'..."
  ibmcloud ce app create \
    --name "$APP_NAME" \
    --build-source . \
    --port 8000 \
    --min-scale 0 \
    --max-scale 3 \
    --env IBM_CLOUD_API_KEY="$IBM_CLOUD_API_KEY" \
    --env WATSONX_PROJECT_ID="$WATSONX_PROJECT_ID" \
    --env WATSONX_URL="${WATSONX_URL:-https://us-south.ml.cloud.ibm.com}" \
    --env OUTPUT_COS_ENDPOINT="${OUTPUT_COS_ENDPOINT:-}" \
    --env OUTPUT_COS_BUCKET="${OUTPUT_COS_BUCKET:-}" \
    --env OUTPUT_COS_ACCESS_KEY="${OUTPUT_COS_ACCESS_KEY:-}" \
    --env OUTPUT_COS_SECRET_KEY="${OUTPUT_COS_SECRET_KEY:-}"
fi

# ── Set APP_URL so the OpenAPI spec reflects the real server ────────

echo ""
echo "==> Fetching deployed app URL..."
APP_URL=$(ibmcloud ce app get --name "$APP_NAME" --output json | jq -r '.status.url')
echo "    $APP_URL"

echo ""
echo "==> Updating APP_URL env var on the app..."
ibmcloud ce app update --name "$APP_NAME" --env APP_URL="$APP_URL"

# Persist to local .env too
if grep -q "^APP_URL=" "$ENV_FILE"; then
  sed -i '' "s|^APP_URL=.*|APP_URL=$APP_URL|" "$ENV_FILE"
else
  echo "APP_URL=$APP_URL" >> "$ENV_FILE"
fi

echo ""
echo "Done."
echo "  App URL : $APP_URL"
echo "  OpenAPI : $APP_URL/openapi.json"
echo "  Swagger : $APP_URL/docs"
