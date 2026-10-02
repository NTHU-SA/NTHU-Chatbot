#!/usr/bin/env bash
# 建立或校正一個環境（staging / prod）的 GCP 資源。可重複執行：已存在的資源只會被校正，不會重建。
#
# 用法：
#   bash infra/bootstrap.sh infra/environments/staging.conf
#   bash infra/bootstrap.sh infra/environments/prod.conf
#   LINE_LOGIN_CHANNEL_ID=... LIFF_ID=... bash infra/bootstrap.sh ...   # 第一次建立或更換 LINE 頻道時
#
# LINE 的 ID 不寫在 repo：由執行者以環境變數注入；沒給時沿用 Cloud Run 服務上現有的值。
#
# staging 與 prod 可以在同一個 GCP 專案：資料庫、Secret、SA、WIF pool、Cloud Run 服務與 Hosting 網站
# 都依 conf 分開命名，權限也只授予自己那一份（Firestore 以 IAM condition 限定資料庫、
# Cloud Run 部署權限只在自己的服務上）。
#
# 這支腳本不處理任何機密值：Secret 只建立「容器」，值由人另外用 stdin 加入（見 infra/README.md）。
# 刪除類操作（服務、資料庫、舊 trigger）一律不做，只印出建議指令。
set -euo pipefail
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

ENV_FILE="${1:?usage: bash infra/bootstrap.sh infra/environments/<name>.conf}"
# shellcheck source=/dev/null
source "$ENV_FILE"

: "${PROJECT_ID:?}" "${REGION:?}" "${SERVICE:?}" "${GITHUB_OWNER:?}" "${GITHUB_REPO:?}" "${BRANCH_REGEX:?}"
: "${OPENAI_MODEL:?}" "${MAX_INSTANCES:?}"
: "${HOSTING_SITE:?}" "${DEPLOY_REF:?}"
if grep -q "REPLACE_ME" "$ENV_FILE"; then
  echo "${ENV_FILE} 還有 REPLACE_ME 沒填" >&2
  exit 1
fi
DELETE_PROTECTION="${DELETE_PROTECTION:-true}"
AR_REPOSITORY="${AR_REPOSITORY:-cloud-run-source-deploy}"
RUNTIME_SA_NAME="${RUNTIME_SA_NAME:-nthu-chatbot}"
DEPLOYER_SA_NAME="${DEPLOYER_SA_NAME:-nthu-chatbot-deployer}"
HOSTING_SA_NAME="${HOSTING_SA_NAME:-nthu-chatbot-hosting}"
WIF_POOL="${WIF_POOL:-github-actions}"
# 同一個專案裡每個環境一個 Firestore 資料庫、一組 Secret（名稱加後綴）
FIRESTORE_DATABASE="${FIRESTORE_DATABASE:-(default)}"
SECRET_SUFFIX="${SECRET_SUFFIX:-}"
WIF_PROVIDER="${WIF_PROVIDER:-github}"
# API 只允許 LIFF 前端（Firebase Hosting）的兩個預設網域跨站呼叫
FRONTEND_ORIGINS="https://${HOSTING_SITE}.web.app,https://${HOSTING_SITE}.firebaseapp.com"
MCP_SERVER_URL="${MCP_SERVER_URL:-https://api.nthusa.tw/mcp}"
OPENAI_USE_RESPONSES_API="${OPENAI_USE_RESPONSES_API:-true}"
REASONING_SUMMARY="${REASONING_SUMMARY:-true}"
WEB_SEARCH_ENABLED="${WEB_SEARCH_ENABLED:-false}"
PYTHON="${PYTHON:-python}"
SECRET_OPENAI="openai-api-key${SECRET_SUFFIX}"
SECRET_LINE_SECRET="line-channel-secret${SECRET_SUFFIX}"
SECRET_LINE_TOKEN="line-channel-access-token${SECRET_SUFFIX}"
SECRETS=("$SECRET_OPENAI" "$SECRET_LINE_SECRET" "$SECRET_LINE_TOKEN")

RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
DEPLOYER_SA="${DEPLOYER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
HOSTING_SA="${HOSTING_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
G=(gcloud --project="$PROJECT_ID" --quiet)

step() { printf '\n==> %s\n' "$*"; }

# 服務上目前的環境變數值（服務不存在時為空）
service_env() {
  "${G[@]}" run services describe "$SERVICE" --region="$REGION" --format=json 2>/dev/null \
    | "$PYTHON" -c 'import json, sys
raw = sys.stdin.read()
env = json.loads(raw)["spec"]["template"]["spec"]["containers"][0].get("env", []) if raw.strip() else []
print(next((e.get("value", "") for e in env if e["name"] == sys.argv[1]), ""))' "$1" | tr -d '\r' || true
}
LINE_LOGIN_CHANNEL_ID="${LINE_LOGIN_CHANNEL_ID:-$(service_env LINE_LOGIN_CHANNEL_ID)}"
LIFF_ID="${LIFF_ID:-$(service_env LIFF_ID)}"
if [[ -z "$LINE_LOGIN_CHANNEL_ID" || -z "$LIFF_ID" ]]; then
  echo "第一次建立服務：請以環境變數提供 LINE_LOGIN_CHANNEL_ID 與 LIFF_ID" >&2
  exit 1
fi

step "啟用 API"
"${G[@]}" services enable \
  run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  firestore.googleapis.com firebaserules.googleapis.com secretmanager.googleapis.com \
  iam.googleapis.com monitoring.googleapis.com logging.googleapis.com \
  firebase.googleapis.com firebasehosting.googleapis.com iamcredentials.googleapis.com sts.googleapis.com

step "Firebase 專案與 Hosting 網站：${HOSTING_SITE}"
token="$(gcloud auth print-access-token)"
fb=(-H "Authorization: Bearer ${token}" -H "x-goog-user-project: ${PROJECT_ID}" -H "Content-Type: application/json")
if curl -fsS "${fb[@]}" "https://firebase.googleapis.com/v1beta1/projects/${PROJECT_ID}" >/dev/null 2>&1; then
  echo "已是 Firebase 專案"
else
  # 把這個 GCP 專案加入 Firebase（不可逆，只能刪除整個專案），Hosting 需要
  curl -fsS -X POST "${fb[@]}" -d '{}' "https://firebase.googleapis.com/v1beta1/projects/${PROJECT_ID}:addFirebase" >/dev/null
  echo "已加入 Firebase（背景作業，約一分鐘完成）"
  for _ in $(seq 1 30); do
    curl -fsS "${fb[@]}" "https://firebase.googleapis.com/v1beta1/projects/${PROJECT_ID}" >/dev/null 2>&1 && break
    sleep 5
  done
fi
if curl -fsS "${fb[@]}" "https://firebasehosting.googleapis.com/v1beta1/projects/${PROJECT_ID}/sites/${HOSTING_SITE}" >/dev/null 2>&1; then
  echo "Hosting 網站已存在"
else
  curl -fsS -X POST "${fb[@]}" -d '{}' \
    "https://firebasehosting.googleapis.com/v1beta1/projects/${PROJECT_ID}/sites?siteId=${HOSTING_SITE}" >/dev/null
  echo "已建立 Hosting 網站"
fi

step "Firestore ${FIRESTORE_DATABASE}，Native mode，${REGION}"
DB=(--database="$FIRESTORE_DATABASE")
if "${G[@]}" firestore databases describe "${DB[@]}" >/dev/null 2>&1; then
  echo "已存在"
else
  protection=()
  [[ "$DELETE_PROTECTION" == "true" ]] && protection=(--delete-protection)
  "${G[@]}" firestore databases create "${DB[@]}" --location="$REGION" \
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
# 非 (default) 資料庫的規則 release 名稱是 cloud.firestore/<資料庫 ID>
release_id="cloud.firestore"
[[ "$FIRESTORE_DATABASE" != "(default)" ]] && release_id="cloud.firestore/${FIRESTORE_DATABASE}"
release_body="{\"name\":\"projects/${PROJECT_ID}/releases/${release_id}\",\"rulesetName\":\"${ruleset}\"}"
if ! curl -fsS -X POST "$release_url" -H "Authorization: Bearer ${token}" -H "x-goog-user-project: ${PROJECT_ID}" \
     -H "Content-Type: application/json" -d "$release_body" >/dev/null 2>&1; then
  curl -fsS -X PATCH "${release_url}/${release_id}" -H "Authorization: Bearer ${token}" \
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
  if "${G[@]}" firestore indexes composite create "${DB[@]}" --collection-group="$group" \
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
  "${G[@]}" firestore indexes fields update "$field" "${DB[@]}" --collection-group="$group" --disable-indexes >/dev/null
  if [[ "$ttl" == "ttl" ]]; then
    "${G[@]}" firestore fields ttls update "$field" "${DB[@]}" --collection-group="$group" --enable-ttl --async >/dev/null
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
for name in "$RUNTIME_SA_NAME" "$DEPLOYER_SA_NAME" "$HOSTING_SA_NAME"; do
  if ! "${G[@]}" iam service-accounts describe "${name}@${PROJECT_ID}.iam.gserviceaccount.com" >/dev/null 2>&1; then
    "${G[@]}" iam service-accounts create "$name" --display-name="$name"
  fi
done

step "執行期 SA 權限：只能讀寫自己的 Firestore 資料庫 + 自己的 Secret（逐一授權）"
db_resource="projects/${PROJECT_ID}/databases/${FIRESTORE_DATABASE}"
db_condition="expression=resource.name == '${db_resource}' || resource.name.startsWith('${db_resource}/'),title=only-${FIRESTORE_DATABASE//[()]/}-database"
"${G[@]}" projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${RUNTIME_SA}" --role=roles/datastore.user --condition="$db_condition" >/dev/null
# 舊版授予的是整個專案（所有資料庫）的權限，移除
"${G[@]}" projects remove-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${RUNTIME_SA}" --role=roles/datastore.user --condition=None >/dev/null 2>&1 || true

step "部署 SA 權限：寫 log、推映像，且只能代理執行期 SA（Cloud Run 權限在服務建立後只授予該服務）"
"${G[@]}" projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${DEPLOYER_SA}" --role=roles/logging.logWriter --condition=None >/dev/null
"${G[@]}" artifacts repositories add-iam-policy-binding "$AR_REPOSITORY" --location="$REGION" \
  --member="serviceAccount:${DEPLOYER_SA}" --role=roles/artifactregistry.writer >/dev/null
"${G[@]}" iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --member="serviceAccount:${DEPLOYER_SA}" --role=roles/iam.serviceAccountUser >/dev/null

step "GitHub Actions 部署前端：Workload Identity Federation（不使用任何金鑰）"
# 只有 ${GITHUB_OWNER}/${GITHUB_REPO} 在 ${DEPLOY_REF} 上執行的 workflow 能換到憑證
project_number="$("${G[@]}" projects describe "$PROJECT_ID" --format='value(projectNumber)')"
if ! "${G[@]}" iam workload-identity-pools describe "$WIF_POOL" --location=global >/dev/null 2>&1; then
  "${G[@]}" iam workload-identity-pools create "$WIF_POOL" --location=global --display-name="GitHub Actions"
fi
condition="assertion.repository == '${GITHUB_OWNER}/${GITHUB_REPO}' && assertion.ref == '${DEPLOY_REF}'"
provider_flags=(
  --location=global --workload-identity-pool="$WIF_POOL"
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref"
  --attribute-condition="$condition"
)
if "${G[@]}" iam workload-identity-pools providers describe "$WIF_PROVIDER" \
     --location=global --workload-identity-pool="$WIF_POOL" >/dev/null 2>&1; then
  "${G[@]}" iam workload-identity-pools providers update-oidc "$WIF_PROVIDER" "${provider_flags[@]}" >/dev/null
else
  "${G[@]}" iam workload-identity-pools providers create-oidc "$WIF_PROVIDER" "${provider_flags[@]}" \
    --issuer-uri="https://token.actions.githubusercontent.com"
fi
# Hosting 部署 SA：只能部署 Hosting
for role in roles/firebasehosting.admin roles/serviceusage.serviceUsageConsumer; do
  "${G[@]}" projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${HOSTING_SA}" --role="$role" --condition=None >/dev/null
done
principal="principalSet://iam.googleapis.com/projects/${project_number}/locations/global/workloadIdentityPools/${WIF_POOL}/attribute.repository/${GITHUB_OWNER}/${GITHUB_REPO}"
"${G[@]}" iam service-accounts add-iam-policy-binding "$HOSTING_SA" \
  --member="$principal" --role=roles/iam.workloadIdentityUser >/dev/null
WIF_PROVIDER_NAME="projects/${project_number}/locations/global/workloadIdentityPools/${WIF_POOL}/providers/${WIF_PROVIDER}"

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
# 以 ^;^ 指定分隔符號：CORS_ALLOWED_ORIGINS 的值本身含逗號
env_vars="^;^CHAT_STORE=firestore;GOOGLE_CLOUD_PROJECT=${PROJECT_ID};FIRESTORE_DATABASE=${FIRESTORE_DATABASE}"
env_vars+=";LINE_LOGIN_CHANNEL_ID=${LINE_LOGIN_CHANNEL_ID}"
env_vars+=";LIFF_ID=${LIFF_ID};OPENAI_MODEL=${OPENAI_MODEL};OPENAI_USE_RESPONSES_API=${OPENAI_USE_RESPONSES_API}"
env_vars+=";REASONING_SUMMARY=${REASONING_SUMMARY};MCP_SERVER_URL=${MCP_SERVER_URL}"
env_vars+=";CORS_ALLOWED_ORIGINS=${FRONTEND_ORIGINS};WEB_SEARCH_ENABLED=${WEB_SEARCH_ENABLED}"
secret_vars="OPENAI_API_KEY=${SECRET_OPENAI}:latest,LINE_CHANNEL_SECRET=${SECRET_LINE_SECRET}:latest"
secret_vars+=",LINE_CHANNEL_ACCESS_TOKEN=${SECRET_LINE_TOKEN}:latest"
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
# 部署 SA 只能更新這個服務（不給專案層級的 run.developer，另一個環境的服務碰不到）
"${G[@]}" run services add-iam-policy-binding "$SERVICE" --region="$REGION" \
  --member="serviceAccount:${DEPLOYER_SA}" --role=roles/run.developer >/dev/null
"${G[@]}" projects remove-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${DEPLOYER_SA}" --role=roles/run.developer --condition=None >/dev/null 2>&1 || true
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
echo "LINE Developers：Webhook URL = ${url}/callback；LIFF Endpoint URL = https://${HOSTING_SITE}.web.app/"
echo "conf 的 API_ORIGIN 應為：${url}（staging 可用 https://${SERVICE}-${project_number}.${REGION}.run.app）"
echo "GitHub repo variables（Settings → Secrets and variables → Actions → Variables，不是機密）："
echo "  GCP_WIF_PROVIDER_<ENV>=${WIF_PROVIDER_NAME}"
echo "  GCP_HOSTING_SA_<ENV>=${HOSTING_SA}"
echo "  LIFF_ID_<ENV>=${LIFF_ID}"
echo "監測與告警：ALERT_EMAIL=you@example.com bash infra/monitoring.sh ${ENV_FILE}"
