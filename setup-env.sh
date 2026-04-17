#!/usr/bin/env bash
# Retrieves IBM Cloud credentials and writes them to .env
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

# ── Helper ─────────────────────────────────────────────────────────────
confirm() { read -rp "$1 [Y/n] " _a; [[ -z "$_a" || "$_a" =~ ^[Yy] ]]; }

# ── Load existing .env ─────────────────────────────────────────────────
if [[ -f "$ENV_FILE" ]]; then
  set -a; source "$ENV_FILE"; set +a
  echo "    Loaded existing .env"
  echo ""
fi

# ── Python dependencies ────────────────────────────────────────────────
echo "==> Installing Python dependencies..."
pip install -r "$SCRIPT_DIR/requirements.txt" -q
echo "    Done."
echo ""

# ── IBM Cloud login ────────────────────────────────────────────────────
echo "==> IBM Cloud login..."
TARGET_OUT=$(ibmcloud target 2>/dev/null || true)
IBMCLOUD_USER=$(echo "$TARGET_OUT" | awk '/^User:/{print $2}')
IBMCLOUD_ACCOUNT=$(echo "$TARGET_OUT" | sed -n 's/^Account: *//p')

if [[ -n "$IBMCLOUD_USER" ]]; then
  echo "    User:    $IBMCLOUD_USER"
  echo "    Account: $IBMCLOUD_ACCOUNT"
  if ! confirm "    Continue with this account?"; then
    ibmcloud login --sso -q
    TARGET_OUT=$(ibmcloud target 2>/dev/null || true)
  fi
else
  echo "    Not logged in."
  ibmcloud login --sso -q
  TARGET_OUT=$(ibmcloud target 2>/dev/null || true)
fi

# ── Target: region + resource group ───────────────────────────────────
echo ""
echo "==> IBM Cloud target..."
CURRENT_REGION=$(echo "$TARGET_OUT" | awk '/^Region:/{print $2}')
CURRENT_RG=$(echo "$TARGET_OUT" | sed -n 's/^Resource group: *//p' | sed 's/ *$//')

echo "    Region:         ${CURRENT_REGION:-not set}"
echo "    Resource group: ${CURRENT_RG:-not set}"
if ! confirm "    Use this target?"; then
  read -rp "    Enter region (e.g. us-south) [${CURRENT_REGION:-us-south}]: " INPUT_REGION
  read -rp "    Enter resource group [${CURRENT_RG:-Default}]: " INPUT_RG
  CURRENT_REGION="${INPUT_REGION:-${CURRENT_REGION:-us-south}}"
  CURRENT_RG="${INPUT_RG:-${CURRENT_RG:-Default}}"
  ibmcloud target -r "$CURRENT_REGION" -g "$CURRENT_RG"
fi
echo ""

# ── IBM Cloud API key ──────────────────────────────────────────────────
echo "==> IBM Cloud API key..."
if [[ -n "${IBM_CLOUD_API_KEY:-}" ]]; then
  echo "    Key already set in .env (${IBM_CLOUD_API_KEY:0:8}…)."
  if ! confirm "    Reuse this key?"; then
    API_KEY_JSON=$(ibmcloud iam api-key-create translator-agent-key \
      -d "watsonx Translator Agent" --output json)
    IBM_CLOUD_API_KEY=$(echo "$API_KEY_JSON" | jq -r '.apikey')
    echo "    Created: $(echo "$API_KEY_JSON" | jq -r '.name')"
  fi
else
  API_KEY_JSON=$(ibmcloud iam api-key-create translator-agent-key \
    -d "watsonx Translator Agent" --output json)
  IBM_CLOUD_API_KEY=$(echo "$API_KEY_JSON" | jq -r '.apikey')
  echo "    Created: $(echo "$API_KEY_JSON" | jq -r '.name')"
fi
echo ""

# ── watsonx project ────────────────────────────────────────────────────
echo "==> watsonx project ID..."
if [[ -n "${WATSONX_PROJECT_ID:-}" ]]; then
  echo "    Current: $WATSONX_PROJECT_ID"
  if confirm "    Use this project?"; then
    echo "    Keeping existing project ID."
  else
    WATSONX_PROJECT_ID=""
  fi
fi

if [[ -z "${WATSONX_PROJECT_ID:-}" ]]; then
  echo "    Fetching IAM token..."
  IAM_TOKEN=$(ibmcloud iam oauth-tokens --output json | jq -r '.iam_token')
  echo "    Fetching watsonx projects..."
  PROJECTS=$(curl -sf -X GET "https://api.dataplatform.cloud.ibm.com/v2/projects" \
    -H "Authorization: $IAM_TOKEN")
  PROJECT_COUNT=$(echo "$PROJECTS" | jq '.total_results')
  if [[ "$PROJECT_COUNT" -eq 0 ]]; then
    echo "    No watsonx projects found. Create one at cloud.ibm.com first."
    exit 1
  elif [[ "$PROJECT_COUNT" -eq 1 ]]; then
    WATSONX_PROJECT_ID=$(echo "$PROJECTS" | jq -r '.resources[0].metadata.guid')
    echo "    Using: $(echo "$PROJECTS" | jq -r '.resources[0].entity.name') ($WATSONX_PROJECT_ID)"
  else
    echo ""
    echo "    Multiple projects found — pick one:"
    echo "$PROJECTS" | jq -r '.resources[] | "    \(.metadata.guid)  \(.entity.name)"'
    echo ""
    read -rp "    Enter project ID: " WATSONX_PROJECT_ID
  fi
fi
echo ""

# ── COS HMAC credentials ───────────────────────────────────────────────
echo "==> COS HMAC credentials..."
if [[ -n "${OUTPUT_COS_ACCESS_KEY:-}" && -n "${OUTPUT_COS_SECRET_KEY:-}" ]]; then
  echo "    Credentials already set in .env (access key: ${OUTPUT_COS_ACCESS_KEY:0:8}…)."
  if confirm "    Reuse these credentials?"; then
    echo "    Keeping existing HMAC credentials."
  else
    OUTPUT_COS_ACCESS_KEY=""
    OUTPUT_COS_SECRET_KEY=""
  fi
fi

if [[ -z "${OUTPUT_COS_ACCESS_KEY:-}" ]]; then
  HMAC_JSON=$(ibmcloud resource service-key translator-hmac --output json 2>/dev/null || true)
  if [[ -z "$HMAC_JSON" || "$HMAC_JSON" == "[]" ]]; then
    echo "    HMAC key 'translator-hmac' not found — creating it..."
    ibmcloud resource service-key-create translator-hmac Writer \
      --instance-name CloudObjectStorage \
      --parameters '{"HMAC": true}' -q
    HMAC_JSON=$(ibmcloud resource service-key translator-hmac --output json)
  fi
  OUTPUT_COS_ACCESS_KEY=$(echo "$HMAC_JSON" | jq -r '.[0].credentials.cos_hmac_keys.access_key_id')
  OUTPUT_COS_SECRET_KEY=$(echo "$HMAC_JSON" | jq -r '.[0].credentials.cos_hmac_keys.secret_access_key')
  echo "    Retrieved HMAC credentials."
fi
echo ""

# ── COS bucket ─────────────────────────────────────────────────────────
echo "==> COS bucket..."
if [[ -n "${OUTPUT_COS_BUCKET:-}" ]]; then
  echo "    Current: $OUTPUT_COS_BUCKET"
  if ! confirm "    Use this bucket?"; then
    read -rp "    Enter your COS bucket name: " OUTPUT_COS_BUCKET
  fi
else
  read -rp "    Enter your COS bucket name: " OUTPUT_COS_BUCKET
fi
echo ""

# ── Code Engine app URL ────────────────────────────────────────────────
echo "==> Fetching Code Engine app URL..."
CE_APP_URL=$(ibmcloud ce app get --name watsonx-translator --output json 2>/dev/null \
  | jq -r '.status.url // empty' 2>/dev/null || true)
if [[ -n "$CE_APP_URL" ]]; then
  APP_URL="$CE_APP_URL"
  echo "    $APP_URL"
elif [[ -n "${APP_URL:-}" && "$APP_URL" != "http://localhost:8000" ]]; then
  echo "    Previously set: $APP_URL"
  if ! confirm "    Keep this URL?"; then
    APP_URL="http://localhost:8000"
    echo "    Defaulting to $APP_URL"
  fi
else
  APP_URL="http://localhost:8000"
  echo "    App not deployed yet — defaulting to $APP_URL"
fi
echo ""

# ── Write .env ─────────────────────────────────────────────────────────
echo "==> Writing $ENV_FILE ..."
cat > "$ENV_FILE" <<EOF
# ── watsonx AI Translator Agent Configuration ───────────────────────
# Generated by setup-env.sh — do not commit this file.

# Required
IBM_CLOUD_API_KEY=$IBM_CLOUD_API_KEY
WATSONX_PROJECT_ID=$WATSONX_PROJECT_ID

# Optional: Override defaults
APP_URL=$APP_URL
WATSONX_URL=${WATSONX_URL:-https://us-south.ml.cloud.ibm.com}
WATSONX_API_VERSION=${WATSONX_API_VERSION:-2024-05-01}
CHUNK_SIZE=${CHUNK_SIZE:-3000}

# Optional: IBM COS output bucket
OUTPUT_COS_ENDPOINT=${OUTPUT_COS_ENDPOINT:-https://s3.us-south.cloud-object-storage.appdomain.cloud}
OUTPUT_COS_BUCKET=$OUTPUT_COS_BUCKET
OUTPUT_COS_ACCESS_KEY=$OUTPUT_COS_ACCESS_KEY
OUTPUT_COS_SECRET_KEY=$OUTPUT_COS_SECRET_KEY
EOF

echo ""
echo "Done. Run to start locally:"
echo "  uvicorn main:app --host 0.0.0.0 --port 8000"
