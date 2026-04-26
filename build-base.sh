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
REPO_URL=$(git -C "$SCRIPT_DIR" remote get-url origin)

echo "==> Base image : $BASE_IMAGE"
echo "==> Git source : $REPO_URL"
echo "    This takes ~5 minutes (apt + pip + fonts). Run only when deps change."
echo ""

# Create or update the build config (uses git repo as source)
if ibmcloud ce build get --name "$BUILD_NAME" &>/dev/null; then
  echo "==> Updating build config '$BUILD_NAME'..."
  ibmcloud ce build update \
    --name "$BUILD_NAME" \
    --source "$REPO_URL" \
    --dockerfile Dockerfile.base \
    --image "$BASE_IMAGE" \
    --registry-secret ce-auto-icr-private-us-south \
    --size large
else
  echo "==> Creating build config '$BUILD_NAME'..."
  ibmcloud ce build create \
    --name "$BUILD_NAME" \
    --source "$REPO_URL" \
    --dockerfile Dockerfile.base \
    --image "$BASE_IMAGE" \
    --registry-secret ce-auto-icr-private-us-south \
    --strategy dockerfile \
    --size large
fi

echo ""
echo "==> Submitting build run..."
ibmcloud ce buildrun submit --build "$BUILD_NAME" --wait

echo ""
echo "Done. Base image ready: $BASE_IMAGE"
echo "Future deploys (./deploy.sh redeploy) will only rebuild main.py (~30s)."
