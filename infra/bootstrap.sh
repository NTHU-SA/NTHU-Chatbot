#!/usr/bin/env bash
# 建立或校正一個環境（staging / prod）的 GCP 資源。可重複執行：已存在的資源只會被校正，不會重建。
#
# 用法：
#   bash infra/bootstrap.sh infra/environments/staging.conf
#   bash infra/bootstrap.sh infra/environments/prod.conf
#
# 這支腳本不處理任何機密值：Secret 只建立「容器」，值由人另外用 stdin 加入（見 infra/README.md）。
# 刪除類操作（服務、資料庫、舊 trigger）一律不做，只印出建議指令。
set -euo pipefail
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

ENV_FILE="${1:?usage: bash infra/bootstrap.sh infra/environments/<name>.conf}"
# shellcheck source=/dev/null
source "$ENV_FILE"

: "${PROJECT_ID:?}" "${REGION:?}" "${SERVICE:?}" "${GITHUB_OWNER:?}" "${GITHUB_REPO:?}" "${BRANCH_REGEX:?}"
: "${LINE_LOGIN_CHANNEL_ID:?}" "${LIFF_ID:?}" "${OPENAI_MODEL:?}" "${MAX_INSTANCES:?}"
if grep -q "REPLACE_ME" "$ENV_FILE"; then
  echo "${ENV_FILE} 還有 REPLACE_ME 沒填" >&2
  exit 1
fi
DELETE_PROTECTION="${DELETE_PROTECTION:-true}"
AR_REPOSITORY="${AR_REPOSITORY:-cloud-run-source-deploy}"
RUNTIME_SA_NAME="${RUNTIME_SA_NAME:-nthu-chatbot}"
DEPLOYER_SA_NAME="${DEPLOYER_SA_NAME:-nthu-chatbot-deployer}"
MCP_SERVER_URL="${MCP_SERVER_URL:-https://api.nthusa.tw/mcp}"
OPENAI_USE_RESPONSES_API="${OPENAI_USE_RESPONSES_API:-true}"
REASONING_SUMMARY="${REASONING_SUMMARY:-true}"
PYTHON="${PYTHON:-python}"
SECRETS=(openai-api-key line-channel-secret line-channel-access-token)

RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
DEPLOYER_SA="${DEPLOYER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
G=(gcloud --project="$PROJECT_ID" --quiet)

step() { printf '\n==> %s\n' "$*"; }

step "啟用 API"
"${G[@]}" services enable \
  run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  firestore.googleapis.com firebaserules.googleapis.com secretmanager.googleapis.com \
  iam.googleapis.com monitoring.googleapis.com logging.googleapis.com

step "Firestore (default)，Native mode，${REGION}"
if "${G[@]}" firestore databases describe --database='(default)' >/dev/null 2>&1; then
  echo "已存在"
else
  protection=()
  [[ "$DELETE_PROTECTION" == "true" ]] && protection=(--delete-protection)
  "${G[@]}" firestore databases create --database='(default)' --location="$REGION" \
    --type=firestore-native "${protection[@]}"
fi

step "Firestore 規則：全部拒絕（只有後端以 Admin SDK / ADC 存取）"
token="$(gcloud auth print-access-token)"
rules_json="$("$PYTHON" - <<'PY'
import json, pathlib
print(json.dumps({"source": {"files": [{"name": "firestore.rules",
      "content": pathlib.Path("firestore.rules").read_text(encoding="utf-8")}]}}))
PY
)"
ruleset="$(curl -fsS -X POST "https://firebaserules.googleapis.com/v1/projects/${PROJECT_ID}/rulesets" \
  -H "Authorization: Bearer ${token}" -H "x-goog-user-project: ${PROJECT_ID}" \
  -H "Content-Type: application/json" -d "$rules_json" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["name"])' | tr -d '\r')"
release_url="https://firebaserules.googleapis.com/v1/projects/${PROJECT_ID}/releases"
release_body="{\"name\":\"projects/${PROJECT_ID}/releases/cloud.firestore\",\"rulesetName\":\"${ruleset}\"}"
if ! curl -fsS -X POST "$release_url" -H "Authorization: Bearer ${token}" -H "x-goog-user-project: ${PROJECT_ID}" \
     -H "Content-Type: application/json" -d "$release_body" >/dev/null 2>&1; then
  curl -fsS -X PATCH "${release_url}/cloud.firestore" -H "Authorization: Bearer ${token}" \
    -H "x-goog-user-project: ${PROJECT_ID}" -H "Content-Type: application/json" \
    -d "{\"release\":${release_body}}" >/dev/null
fi
echo "已發布 ${ruleset}"

step "Firestore 索引與 TTL（firestore.indexes.json）"
# 複合索引：已存在時 gcloud 會回錯誤，視為成功
"$PYTHON" - <<'PY' | tr -d '\r' | while read -r group fields; do
import json, pathlib
for index in json.loads(pathlib.Path("firestore.indexes.json").read_text())["indexes"]:
    spec = " ".join(
        f"--field-config=field-path={f['fieldPath']},order={f['order'].lower()}"
        for f in index["fields"]
    )
    print(index["collectionGroup"], spec)
PY
  # shellcheck disable=SC2086
  if "${G[@]}" firestore indexes composite create --collection-group="$group" \
       --query-scope=collection $fields --async >/dev/null 2>&1; then
    echo "  建立中：${group} ${fields}"
  else
    echo "  已存在：${group} ${fields}"
  fi
done
# 單欄位：大型欄位不建索引；expiresAt 開啟 TTL 自動刪除
"$PYTHON" - <<'PY' | tr -d '\r' | while read -r group field ttl; do
import json, pathlib
for o in json.loads(pathlib.Path("firestore.indexes.json").read_text())["fieldOverrides"]:
    print(o["collectionGroup"], o["fieldPath"], "ttl" if o.get("ttl") else "-")
PY
  "${G[@]}" firestore indexes fields update "$field" --collection-group="$group" --disable-indexes >/dev/null
  if [[ "$ttl" == "ttl" ]]; then
    "${G[@]}" firestore fields ttls update "$field" --collection-group="$group" --enable-ttl --async >/dev/null
  fi
  echo "  ${group}.${field} ${ttl}"
done

step "Artifact Registry：${AR_REPOSITORY}（保留最新 3 個、刪除 7 天前）"
if ! "${G[@]}" artifacts repositories describe "$AR_REPOSITORY" --location="$REGION" >/dev/null 2>&1; then
  "${G[@]}" artifacts repositories create "$AR_REPOSITORY" --location="$REGION" --repository-format=docker
fi
policy_file="$(mktemp)"
cat >"$policy_file" <<'JSON'
[
  {"name": "keep-latest-3", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 3}},
  {"name": "delete-older-than-7d", "action": {"type": "Delete"}, "condition": {"olderThan": "7d"}}
]
JSON
"${G[@]}" artifacts repositories set-cleanup-policies "$AR_REPOSITORY" --location="$REGION" \
  --policy="$policy_file" --no-dry-run >/dev/null
rm -f "$policy_file"

step "Service accounts"
for name in "$RUNTIME_SA_NAME" "$DEPLOYER_SA_NAME"; do
  if ! "${G[@]}" iam service-accounts describe "${name}@${PROJECT_ID}.iam.gserviceaccount.com" >/dev/null 2>&1; then
    "${G[@]}" iam service-accounts create "$name" --display-name="$name"
  fi
done

step "執行期 SA 權限：Firestore 讀寫 + 各 Secret 的讀取（逐一授權，不給專案層級）"
"${G[@]}" projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${RUNTIME_SA}" --role=roles/datastore.user --condition=None >/dev/null

step "部署 SA 權限：只能部署 Cloud Run、推映像、寫 log，且只能代理執行期 SA"
for role in roles/run.developer roles/logging.logWriter; do
  "${G[@]}" projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${DEPLOYER_SA}" --role="$role" --condition=None >/dev/null
done
"${G[@]}" artifacts repositories add-iam-policy-binding "$AR_REPOSITORY" --location="$REGION" \
  --member="serviceAccount:${DEPLOYER_SA}" --role=roles/artifactregistry.writer >/dev/null
"${G[@]}" iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --member="serviceAccount:${DEPLOYER_SA}" --role=roles/iam.serviceAccountUser >/dev/null

step "Secret Manager"
missing_values=()
for secret in "${SECRETS[@]}"; do
  if ! "${G[@]}" secrets describe "$secret" >/dev/null 2>&1; then
    "${G[@]}" secrets create "$secret" --replication-policy=automatic
  fi
  "${G[@]}" secrets add-iam-policy-binding "$secret" \
    --member="serviceAccount:${RUNTIME_SA}" --role=roles/secretmanager.secretAccessor >/dev/null
  if [[ -z "$("${G[@]}" secrets versions list "$secret" --filter='state:ENABLED' --limit=1 --format='value(name)')" ]]; then
    missing_values+=("$secret")
  fi
done
if ((${#missing_values[@]})); then
  echo
  echo "下列 Secret 還沒有值，請依 infra/README.md 用 stdin 加入後再重跑本腳本："
  printf '  %s\n' "${missing_values[@]}"
  exit 1
fi

step "Cloud Run：${SERVICE}"
env_vars="CHAT_STORE=firestore,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},LINE_LOGIN_CHANNEL_ID=${LINE_LOGIN_CHANNEL_ID}"
env_vars+=",LIFF_ID=${LIFF_ID},OPENAI_MODEL=${OPENAI_MODEL},OPENAI_USE_RESPONSES_API=${OPENAI_USE_RESPONSES_API}"
env_vars+=",REASONING_SUMMARY=${REASONING_SUMMARY},MCP_SERVER_URL=${MCP_SERVER_URL}"
secret_vars="OPENAI_API_KEY=openai-api-key:latest,LINE_CHANNEL_SECRET=line-channel-secret:latest"
secret_vars+=",LINE_CHANNEL_ACCESS_TOKEN=line-channel-access-token:latest"
service_flags=(
  --region="$REGION"
  --service-account="$RUNTIME_SA"
  --set-env-vars="$env_vars"
  --set-secrets="$secret_vars"
  --timeout=180 --concurrency=40 --cpu=1 --memory=512Mi
  --min-instances=0 --max-instances="$MAX_INSTANCES"
  --cpu-boost
)
if "${G[@]}" run services describe "$SERVICE" --region="$REGION" >/dev/null 2>&1; then
  "${G[@]}" run services update "$SERVICE" "${service_flags[@]}"
else
  # 第一次先用官方 hello 映像建立服務，之後由 Cloud Build trigger 換成實際映像
  "${G[@]}" run deploy "$SERVICE" --image=us-docker.pkg.dev/cloudrun/container/hello "${service_flags[@]}"
fi
# LINE webhook 與 LIFF API 必須能從公網呼叫；API 自己驗證簽章與 id_token
"${G[@]}" run services add-iam-policy-binding "$SERVICE" --region="$REGION" \
  --member=allUsers --role=roles/run.invoker >/dev/null

step "Cloud Build trigger：${GITHUB_OWNER}/${GITHUB_REPO} ${BRANCH_REGEX} → ${SERVICE}"
trigger_name="deploy-${SERVICE}"
substitutions="_AR_HOSTNAME=${REGION}-docker.pkg.dev,_AR_REPOSITORY=${AR_REPOSITORY},_DEPLOY_REGION=${REGION},_SERVICE_NAME=${SERVICE}"
if "${G[@]}" builds triggers describe "$trigger_name" >/dev/null 2>&1; then
  echo "已存在（設定變更請用 gcloud builds triggers update）"
elif ! "${G[@]}" builds triggers create github --name="$trigger_name" \
    --repo-owner="$GITHUB_OWNER" --repo-name="$GITHUB_REPO" --branch-pattern="$BRANCH_REGEX" \
    --build-config=cloudbuild.yaml --substitutions="$substitutions" \
    --service-account="projects/${PROJECT_ID}/serviceAccounts/${DEPLOYER_SA}"; then
  echo "建立失敗：多半是這個專案還沒連上 GitHub。請先在 Console → Cloud Build → Repositories"
  echo "連結 ${GITHUB_OWNER}/${GITHUB_REPO}（安裝 Cloud Build GitHub App），再重跑本腳本。"
  exit 1
fi

url="$("${G[@]}" run services describe "$SERVICE" --region="$REGION" --format='value(status.url)')"
step "完成"
echo "服務網址：${url}"
echo "LINE Developers：Webhook URL = ${url}/callback；LIFF Endpoint URL 之後改成 Firebase Hosting 網址"
echo "監測與告警：ALERT_EMAIL=you@example.com bash infra/monitoring.sh ${ENV_FILE}"
