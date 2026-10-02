#!/usr/bin/env bash
# 手動部署 LIFF 前端到 Firebase Hosting（平常由 CI 在 push 到 dev / main 後自動部署）。
#
#   LIFF_ID=... bash infra/deploy_frontend.sh infra/environments/staging.conf
#   （沒給 LIFF_ID 時沿用該環境 Cloud Run 服務上的值）
#
# 使用目前 gcloud / ADC 登入的身分；不需要也不使用任何金鑰檔。
set -euo pipefail

CONF="${1:?usage: bash infra/deploy_frontend.sh infra/environments/<env>.conf}"
PYTHON="${PYTHON:-python}"
project="$(grep -E '^PROJECT_ID=' "$CONF" | cut -d= -f2 | tr -d '\r')"
region="$(grep -E '^REGION=' "$CONF" | cut -d= -f2 | tr -d '\r')"
service="$(grep -E '^SERVICE=' "$CONF" | cut -d= -f2 | tr -d '\r')"
if [[ -z "${LIFF_ID:-}" ]]; then
  LIFF_ID="$(gcloud run services describe "$service" --project="$project" --region="$region" \
    --format='value(spec.template.spec.containers[0].env.filter(name=LIFF_ID).extract(value).flatten())' | tr -d '\r')"
fi
export LIFF_ID
out="$("$PYTHON" infra/build_frontend.py "$CONF" | tr -d '\r')"
npx --yes firebase-tools@15.32.1 deploy --only hosting --non-interactive \
  --project "$project" --config "${out}/firebase.json"
