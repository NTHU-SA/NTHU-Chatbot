#!/usr/bin/env bash
# 建立監測與告警：email 通知、5xx 與 ERROR log 告警；可選的 GCP uptime check。可重複執行。
#
# 防冷啟動與存活檢查預設交給外部 ping 服務（GET /ping，間隔 ≤ 10 分鐘），所以預設不建 uptime check。
# 用法（email 只從環境變數讀，不寫進 repo）：
#   ALERT_EMAIL=you@example.com bash infra/monitoring.sh infra/environments/prod.conf
#   UPTIME_CHECK=true ALERT_EMAIL=... bash infra/monitoring.sh ...   # 另外建 GCP uptime check 與它的告警
#   UPTIME_CHECK=true bash infra/monitoring.sh ...                   # 只建 uptime check
set -euo pipefail
# Git Bash（Windows）會把 --path=/ping 改寫成檔案路徑；只排除這個參數（Linux 上此變數無作用）
export MSYS2_ARG_CONV_EXCL="--path="
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

ENV_FILE="${1:?usage: [UPTIME_CHECK=true] [ALERT_EMAIL=...] bash infra/monitoring.sh infra/environments/<name>.conf}"
# shellcheck source=/dev/null
source "$ENV_FILE"
: "${PROJECT_ID:?}" "${REGION:?}" "${SERVICE:?}"
PYTHON="${PYTHON:-python}"
UPTIME_CHECK="${UPTIME_CHECK:-false}"
UPTIME_PERIOD_MINUTES="${UPTIME_PERIOD_MINUTES:-5}"
# 5 分鐘內超過幾個 5xx 就告警
ERROR_5XX_THRESHOLD="${ERROR_5XX_THRESHOLD:-5}"
G=(gcloud --project="$PROJECT_ID" --quiet)

step() { printf '\n==> %s\n' "$*"; }

check_id=""
if [[ "$UPTIME_CHECK" == "true" ]]; then
  step "Uptime check：GET /ping，每 ${UPTIME_PERIOD_MINUTES} 分鐘（同時避免實例閒置被回收）"
  url="$("${G[@]}" run services describe "$SERVICE" --region="$REGION" --format='value(status.url)')"
  host="${url#https://}"
  check_name="${SERVICE}-ping"
  # list-configs 不支援以 displayName 篩選：列出全部後自己比對
  check_id="$("${G[@]}" monitoring uptime list-configs --format='value(name,displayName)' \
    | tr -d '\r' | awk -v want="$check_name" '$2 == want { print $1; exit }')"
  if [[ -z "$check_id" ]]; then
    check_id="$("${G[@]}" monitoring uptime create "$check_name" \
      --resource-type=uptime-url --resource-labels="host=${host},project_id=${PROJECT_ID}" \
      --protocol=https --path=/ping --period="$UPTIME_PERIOD_MINUTES" --timeout=10 \
      --matcher-content=pong --matcher-type=contains-string --format='value(name)')"
  fi
  check_id="${check_id##*/}"
  echo "$check_id"
else
  step "略過 GCP uptime check（UPTIME_CHECK=false）：防冷啟動與存活檢查由外部 ping 服務負責"
fi

if [[ -z "${ALERT_EMAIL:-}" ]]; then
  step "完成"
  echo "沒有設定 ALERT_EMAIL，略過通知管道與告警；決定好告警對象後再帶 ALERT_EMAIL 重跑即可。"
  exit 0
fi

step "Email 通知管道"
channel="$("${G[@]}" beta monitoring channels list \
  --filter="type=\"email\" AND labels.email_address=\"${ALERT_EMAIL}\"" --format='value(name)' --limit=1)"
if [[ -z "$channel" ]]; then
  channel="$("${G[@]}" beta monitoring channels create --type=email \
    --display-name="nthu-chatbot alerts" --channel-labels="email_address=${ALERT_EMAIL}" \
    --format='value(name)')"
fi
echo "$channel"

step "Log-based metric：${SERVICE} 的 ERROR 以上 log"
metric="app_errors_${SERVICE//-/_}"
filter="resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${SERVICE}\" AND severity>=ERROR"
if "${G[@]}" logging metrics describe "$metric" >/dev/null 2>&1; then
  "${G[@]}" logging metrics update "$metric" --log-filter="$filter" >/dev/null
else
  "${G[@]}" logging metrics create "$metric" --description="ERROR+ logs from ${SERVICE}" --log-filter="$filter"
fi

step "告警政策"
policy_dir="$(mktemp -d)"
SERVICE="$SERVICE" CHECK_ID="$check_id" METRIC="$metric" CHANNEL="$channel" \
THRESHOLD="$ERROR_5XX_THRESHOLD" OUT="$policy_dir" "$PYTHON" - <<'PY'
import json, os, pathlib

service, channel, out = os.environ["SERVICE"], os.environ["CHANNEL"], pathlib.Path(os.environ["OUT"])
run = f'resource.type="cloud_run_revision" AND resource.labels.service_name="{service}"'


def policy(name, documentation, condition):
    return {
        "displayName": f"{service}: {name}",
        "documentation": {"content": documentation, "mimeType": "text/markdown"},
        "combiner": "OR",
        "conditions": [{"displayName": name, **condition}],
        "notificationChannels": [channel],
        "alertStrategy": {"autoClose": "1800s"},
    }


policies = {
}
# 沒建 uptime check 時不建它的告警
if os.environ["CHECK_ID"]:
    policies["uptime"] = policy(
        "uptime check failing",
        f"`/ping` 連續失敗。檢查 Cloud Run `{service}` 的 revision 與啟動 log。",
        {
            "conditionThreshold": {
                "filter": (
                    'metric.type="monitoring.googleapis.com/uptime_check/check_passed" '
                    'AND resource.type="uptime_url" '
                    f'AND metric.labels.check_id="{os.environ["CHECK_ID"]}"'
                ),
                "aggregations": [{
                    "alignmentPeriod": "1200s",
                    "perSeriesAligner": "ALIGN_NEXT_OLDER",
                    "crossSeriesReducer": "REDUCE_COUNT_FALSE",
                    "groupByFields": ["resource.label.*"],
                }],
                "comparison": "COMPARISON_GT",
                "thresholdValue": 1,
                "duration": "60s",
                "trigger": {"count": 1},
            }
        },
    )

policies |= {
    "5xx": policy(
        "5xx responses",
        f"`{service}` 5 分鐘內回了超過 {os.environ['THRESHOLD']} 個 5xx。",
        {
            "conditionThreshold": {
                "filter": (
                    f'{run} AND metric.type="run.googleapis.com/request_count" '
                    'AND metric.labels.response_code_class="5xx"'
                ),
                "aggregations": [{
                    "alignmentPeriod": "300s",
                    "perSeriesAligner": "ALIGN_DELTA",
                    "crossSeriesReducer": "REDUCE_SUM",
                }],
                "comparison": "COMPARISON_GT",
                "thresholdValue": int(os.environ["THRESHOLD"]),
                "duration": "0s",
                "trigger": {"count": 1},
            }
        },
    ),
    "errors": policy(
        "application errors",
        f"`{service}` 出現 ERROR 以上的 log（例如 `Chat stream failed`、`MCP connect failed`）。"
        "到 Logs Explorer 以 severity>=ERROR 篩選。",
        {
            "conditionThreshold": {
                "filter": (
                    f'{run} AND metric.type="logging.googleapis.com/user/{os.environ["METRIC"]}"'
                ),
                "aggregations": [{
                    "alignmentPeriod": "300s",
                    "perSeriesAligner": "ALIGN_DELTA",
                    "crossSeriesReducer": "REDUCE_SUM",
                }],
                "comparison": "COMPARISON_GT",
                "thresholdValue": 0,
                "duration": "0s",
                "trigger": {"count": 1},
            }
        },
    ),
}
for key, body in policies.items():
    (out / f"{key}.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
PY
for file in "$policy_dir"/*.json; do
  name="$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["displayName"])' "$file" | tr -d '\r')"
  existing="$("${G[@]}" monitoring policies list --filter="displayName=\"${name}\"" --format='value(name)' --limit=1)"
  if [[ -n "$existing" ]]; then
    "${G[@]}" monitoring policies update "$existing" --policy-from-file="$file" >/dev/null
    echo "已更新：${name}"
  else
    "${G[@]}" monitoring policies create --policy-from-file="$file" >/dev/null
    echo "已建立：${name}"
  fi
done
rm -rf "$policy_dir"

step "完成"
echo "到 ${ALERT_EMAIL} 收信確認通知管道（第一次建立時 Google 不需驗證，但請確認沒被歸到垃圾郵件）。"
