#!/usr/bin/env bash
# Builds the heavy base image (system libs + pip packages + fonts) and pushes it
# to IBM Container Registry. Run this ONCE, or whenever requirements.txt or
# Dockerfile.base changes. Normal code deploys (./deploy.sh redeploy) are fast
# because they only rebuild the thin layer that copies main.py.
#
# Usage:
#   ./build-base.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: .env not found. Run ./setup-env.sh first."
  exit 1
fi
set -a; source "$ENV_FILE"; set +a

ICR_NAMESPACE="${ICR_NAMESPACE:-ce--8ff6f-2907fwm9n6us}"
BASE_IMAGE="private.us.icr.io/${ICR_NAMESPACE}/watsonx-translator-base:latest"

echo "==> Building base image: $BASE_IMAGE"
echo "    This takes ~5 minutes (apt + pip + fonts). Run only when deps change."
echo ""

RUN_NAME=$(ibmcloud ce buildrun submit \
  --build-source . \
  --dockerfile Dockerfile.base \
  --image "$BASE_IMAGE" \
  --registry-secret ce-auto-icr-private-us-south \
  --size large \
  --output json | jq -r '.metadata.name')

echo "==> Build run submitted: $RUN_NAME"
echo ""
echo "==> Following logs (Ctrl+C stops following — build continues in background)..."
ibmcloud ce buildrun logs -f -n "$RUN_NAME" || true

echo ""
echo "==> Build status:"
ibmcloud ce buildrun get -n "$RUN_NAME" | grep -E "Status|Reason|Age"

echo ""
echo "Done. Base image ready: $BASE_IMAGE"
echo "Future deploys (./deploy.sh redeploy) will only rebuild main.py (~30s)."
