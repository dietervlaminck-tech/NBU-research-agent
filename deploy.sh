#!/usr/bin/env bash
#
# One-command deploy to Azure Web App (Docker via ACR).
# Idempotent: safe to re-run for the first deploy and every deploy after.
#
# Usage:
#   1. cp .deploy.env.example .deploy.env   and fill it in (it is gitignored)
#   2. az login
#   3. ./deploy.sh
#
# What it does: builds the image in ACR, creates the Web App if missing, sets
# app settings (secrets), mounts Azure Files for the SQLite database, enables
# Always On, points the app at the freshly built image, and restarts it.

set -euo pipefail

cd "$(dirname "$0")"

# --- load config -------------------------------------------------------------
if [ -f .deploy.env ]; then
  # shellcheck disable=SC1091
  set -a; . ./.deploy.env; set +a
fi

require() {
  local name="$1"
  if [ -z "${!name:-}" ]; then
    echo "ERROR: $name is not set. Add it to .deploy.env (see .deploy.env.example)." >&2
    exit 1
  fi
}

require RESOURCE_GROUP
require APP_PLAN
require APP_NAME
require ACR_NAME
require STORAGE_ACCOUNT
require ANTHROPIC_API_KEY
require SECRET_KEY

IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD 2>/dev/null || echo latest)}"
IMAGE="${ACR_NAME}.azurecr.io/${APP_NAME}:${IMAGE_TAG}"
FILE_SHARE="${FILE_SHARE:-nbu-research-data}"

echo "==> Building image ${IMAGE} in ACR ${ACR_NAME}"
az acr build --registry "$ACR_NAME" --image "${APP_NAME}:${IMAGE_TAG}" .

# --- create the web app on first run ----------------------------------------
if ! az webapp show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" >/dev/null 2>&1; then
  echo "==> Creating Web App ${APP_NAME}"
  az webapp create \
    --resource-group "$RESOURCE_GROUP" \
    --plan "$APP_PLAN" \
    --name "$APP_NAME" \
    --deployment-container-image-name "$IMAGE"
else
  echo "==> Web App ${APP_NAME} already exists — updating image"
fi

echo "==> Setting app settings (secrets live in Azure, never in git)"
az webapp config appsettings set --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" --settings \
  ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  SECRET_KEY="$SECRET_KEY" \
  NBU_DATA_DIR="/app/data" \
  WEBSITES_PORT="8000" >/dev/null

echo "==> Ensuring Azure Files mount for the SQLite database"
if ! az webapp config storage-account list --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
      --query "[?customId=='data']" -o tsv | grep -q .; then
  az webapp config storage-account add \
    --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
    --custom-id data --storage-type AzureFiles \
    --share-name "$FILE_SHARE" \
    --mount-path /app/data \
    --account-name "$STORAGE_ACCOUNT" >/dev/null
else
  echo "    (mount 'data' already configured)"
fi

echo "==> Enabling Always On (keeps the background-job worker alive)"
az webapp config set --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" --always-on true >/dev/null

echo "==> Pointing the app at ${IMAGE} and restarting"
az webapp config container set \
  --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
  --docker-custom-image-name "$IMAGE" >/dev/null
az webapp restart --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" >/dev/null

URL="https://$(az webapp show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" --query defaultHostName -o tsv)"
echo "==> Done. Live at ${URL}"
