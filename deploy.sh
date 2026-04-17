#!/usr/bin/env bash
# Deploys the watsonx Translator Agent to IBM Code Engine.
# Run setup-env.sh first to populate .env with credentials.
#
# Usage:
#   ./deploy.sh            — interactive deploy (confirms account, target, project)
#   ./deploy.sh redeploy   — redeploy silently using current ibmcloud context
#   ./deploy.sh openapi    — fetch and save openapi-spec.json from the live app
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
CMD="${1:-}"

# ── Helper ─────────────────────────────────────────────────────────────
confirm() { read -rp "$1 [Y/n] " _a; [[ -z "$_a" || "$_a" =~ ^[Yy] ]]; }

# ── Load .env ──────────────────────────────────────────────────────────
if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: .env not found. Run ./setup-env.sh first."
  exit 1
fi
set -a; source "$ENV_FILE"; set +a

APP_NAME="${APP_NAME:-watsonx-translator}"

# ══ openapi ════════════════════════════════════════════════════════════
if [[ "$CMD" == "openapi" ]]; then
  if [[ -z "${APP_URL:-}" ]]; then
    echo "ERROR: APP_URL not set in .env. Deploy the app first."
    exit 1
  fi
  echo "==> Generating openapi-spec.json from $APP_URL ..."
  curl -sf "$APP_URL/openapi.json" | jq '.' > "$SCRIPT_DIR/openapi-spec.json"
  echo "    Saved to openapi-spec.json"
  exit 0
fi

# ══ redeploy ═══════════════════════════════════════════════════════════
if [[ "$CMD" == "redeploy" ]]; then
  echo "==> Redeploying '$APP_NAME' (no prompts)..."
  if ibmcloud ce app get --name "$APP_NAME" &>/dev/null; then
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
    echo "ERROR: App '$APP_NAME' not found. Run ./deploy.sh first."
    exit 1
  fi

  echo ""
  echo "==> Fetching deployed app URL..."
  APP_URL=$(ibmcloud ce app get --name "$APP_NAME" --output json | jq -r '.status.url')
  echo "    $APP_URL"
  ibmcloud ce app update --name "$APP_NAME" --env APP_URL="$APP_URL"
  if grep -q "^APP_URL=" "$ENV_FILE"; then
    sed -i '' "s|^APP_URL=.*|APP_URL=$APP_URL|" "$ENV_FILE"
  else
    echo "APP_URL=$APP_URL" >> "$ENV_FILE"
  fi

  echo ""
  echo "==> Generating openapi-spec.json..."
  curl -sf "$APP_URL/openapi.json" | jq '.' > "$SCRIPT_DIR/openapi-spec.json"
  echo "    Saved to openapi-spec.json"

  echo ""
  echo "Done."
  echo "  App URL : $APP_URL"
  echo "  Swagger : $APP_URL/docs"
  exit 0
fi

# ══ default: interactive deploy ════════════════════════════════════════

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
fi
ibmcloud target -r "$CURRENT_REGION" -g "$CURRENT_RG"
echo ""

# ── Code Engine project ────────────────────────────────────────────────
echo "==> Code Engine project..."
CURRENT_CE_PROJECT=$(ibmcloud ce project current --output json 2>/dev/null \
  | jq -r '.name // empty' 2>/dev/null || true)

if [[ -n "$CURRENT_CE_PROJECT" ]]; then
  echo "    Currently selected: $CURRENT_CE_PROJECT"
  if ! confirm "    Deploy to this project?"; then
    read -rp "    Enter Code Engine project name: " CURRENT_CE_PROJECT
    ibmcloud ce project select --name "$CURRENT_CE_PROJECT"
  fi
else
  read -rp "    No project selected. Enter Code Engine project name: " CURRENT_CE_PROJECT
  ibmcloud ce project select --name "$CURRENT_CE_PROJECT"
fi
echo ""

# ── Deploy or update ───────────────────────────────────────────────────
if ibmcloud ce app get --name "$APP_NAME" &>/dev/null; then
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

# ── Set APP_URL so the OpenAPI spec reflects the real server ───────────
echo ""
echo "==> Fetching deployed app URL..."
APP_URL=$(ibmcloud ce app get --name "$APP_NAME" --output json | jq -r '.status.url')
echo "    $APP_URL"

echo ""
echo "==> Updating APP_URL env var on the app..."
ibmcloud ce app update --name "$APP_NAME" --env APP_URL="$APP_URL"

if grep -q "^APP_URL=" "$ENV_FILE"; then
  sed -i '' "s|^APP_URL=.*|APP_URL=$APP_URL|" "$ENV_FILE"
else
  echo "APP_URL=$APP_URL" >> "$ENV_FILE"
fi

# ── Generate openapi-spec.json ─────────────────────────────────────────
echo ""
echo "==> Generating openapi-spec.json..."
curl -sf "$APP_URL/openapi.json" | jq '.' > "$SCRIPT_DIR/openapi-spec.json"
echo "    Saved to openapi-spec.json"

echo ""
echo "Done."
echo "  App URL : $APP_URL"
echo "  OpenAPI : $APP_URL/openapi.json"
echo "  Swagger : $APP_URL/docs"
