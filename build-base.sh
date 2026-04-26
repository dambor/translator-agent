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
BUILD_NAME="watsonx-translator-base"

echo "==> Base image : $BASE_IMAGE"
echo "==> Build name : $BUILD_NAME"
echo ""

# Create the build config if it doesn't exist yet
if ! ibmcloud ce build get --name "$BUILD_NAME" &>/dev/null; then
  echo "==> Creating build config '$BUILD_NAME'..."
  ibmcloud ce build create \
    --name "$BUILD_NAME" \
    --build-source . \
    --dockerfile Dockerfile.base \
    --image "$BASE_IMAGE" \
    --registry-secret ce-auto-icr-private-us-south \
    --strategy dockerfile-medium \
    --size large
else
  echo "==> Updating build config '$BUILD_NAME'..."
  ibmcloud ce build update \
    --name "$BUILD_NAME" \
    --build-source . \
    --dockerfile Dockerfile.base \
    --image "$BASE_IMAGE"
fi

echo ""
echo "==> Submitting base image build run..."
RUN_NAME=$(ibmcloud ce buildrun submit --build "$BUILD_NAME" --output json | jq -r '.metadata.name')
echo "    Build run: $RUN_NAME"
echo ""
echo "==> Following build logs (Ctrl+C to stop following — build continues in background)..."
ibmcloud ce buildrun logs -f -n "$RUN_NAME" || true

echo ""
echo "==> Waiting for build to complete..."
ibmcloud ce buildrun get -n "$RUN_NAME"

echo ""
echo "Done. Base image is ready."
echo "Future deploys (./deploy.sh redeploy) will use this base and only rebuild main.py (~30s)."
