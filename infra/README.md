# 環境建置（staging / prod）

兩個環境放在**同一個 GCP 專案**（`nthusa-chatbot`），但所有資源都分開命名，權限也只授予自己那一份：

| | staging | prod |
|---|---|---|
| 設定檔 | `infra/environments/staging.conf` | `infra/environments/prod.conf` |
| GCP 專案 | `nthusa-chatbot` | `nthusa-chatbot`（同一個） |
| Firestore 資料庫 | `(default)` | `prod` |
| Secret | `openai-api-key`、`line-channel-secret`、`line-channel-access-token` | 同名加 `-prod` |
| Service accounts | `nthu-chatbot`、`nthu-chatbot-deployer`、`nthu-chatbot-hosting` | `nthu-chatbot-prod`、`nthu-chatbot-prod-deployer`、`nthu-chatbot-prod-hosting` |
| WIF pool（GitHub Actions 部署前端） | `github-actions`（只信任 `refs/heads/dev`） | `github-actions-prod`（只信任 `refs/heads/main`） |
| 部署來源 | `NTHU-SA/NTHU-Chatbot` 的 `dev` | `NTHU-SA/NTHU-Chatbot` 的 `main` |
| Cloud Run | `nthu-chatbot-staging` | `nthu-chatbot` |
| LINE | 測試用 Provider 的頻道 | 正式 Provider（Messaging API 與 LINE Login 必須在**同一個 Provider**） |
| Firestore 刪除保護 | 關 | 開 |
| LIFF 前端 | `nthusa-chatbot.web.app`（Firebase Hosting） | `nthusa-chatbot-prod.web.app` |

權限隔離：
- 執行期 SA 的 `roles/datastore.user` 帶 IAM condition，只能存取自己的 Firestore 資料庫；Secret 逐一授權。
- 部署 SA 的 `roles/run.developer` 只授予自己的 Cloud Run 服務（不是整個專案），只能代理自己的執行期 SA。
- WIF 每個環境一個 pool：principalSet 是以 pool 為範圍，共用 pool 時 `dev` 的 token 也能代理 prod 的 SA。
- 仍共用的部分：帳單、專案層級的 Owner / Editor、Artifact Registry repo（映像依服務分開路徑），以及 Hosting 部署 SA 的 `firebasehosting.admin`（專案層級，靠 branch protection 與 WIF 的 ref 限制保護）。正式資料的存取請只給少數維運帳號。

兩個環境共用 repo 根目錄的 `cloudbuild.yaml`，差別只在 trigger 的 substitutions。
前端由 CI（`.github/workflows/ci.yml` 的 `deploy-frontend`）部署到 Firebase Hosting；`infra/build_frontend.py` 依環境設定檔產生 `config.json`（只有公開的 LIFF ID 與 API 網址）和 CSP（`connect-src` 只允許該環境的 API）。

## LINE 頻道設定（不寫在 repo）

LINE 的 ID 和金鑰都依環境注入，repo 裡的設定檔不含任何 LINE 頻道資訊：

| 值 | 放在哪裡 | 怎麼設定 |
|---|---|---|
| `LINE_LOGIN_CHANNEL_ID`、`LIFF_ID`（後端） | 該環境 Cloud Run 的環境變數 | 第一次建立或更換頻道時：`LINE_LOGIN_CHANNEL_ID=... LIFF_ID=... bash infra/bootstrap.sh infra/environments/<env>.conf`；之後重跑 bootstrap 不帶這兩個變數會沿用服務上的值 |
| `LIFF_ID`（前端 `config.json`） | GitHub repo variable `LIFF_ID_STAGING` / `LIFF_ID_PROD` | `gh variable set LIFF_ID_<ENV> -R NTHU-SA/NTHU-Chatbot --body <LIFF ID>`；沒設定時 CI 跳過前端部署。手動部署（`deploy_frontend.sh`）沒給 `LIFF_ID` 時讀 Cloud Run 上的值 |
| channel secret、access token | Secret Manager（prod 的名稱加 `-prod`） | 用 stdin 加入（見下方「加入 Secret 值」） |

更換頻道時後端與前端要一起換，否則 LIFF 的 id_token 會因 channel ID 不符被拒絕。

## 腳本

在 repo 根目錄，用 Git Bash 執行（需要已登入的 gcloud、curl、Python）：

```bash
bash infra/bootstrap.sh infra/environments/<env>.conf                          # 建立 / 校正資源
ALERT_EMAIL=you@example.com bash infra/monitoring.sh infra/environments/<env>.conf  # 5xx 與 ERROR log 告警
UPTIME_CHECK=true bash infra/monitoring.sh infra/environments/<env>.conf          # 選用：另外建 GCP uptime check
```

兩支腳本都可以重複執行：已存在的資源只會被校正。刪除類操作一律不做，只在下面列出指令，由人確認後手動執行。
Windows 上如果 `python` 不是正確的直譯器，可以加 `PYTHON=.venv/Scripts/python.exe`。

### `bootstrap.sh` 做的事

1. 啟用需要的 API；把專案加入 Firebase（**不可逆**，只能刪除整個專案）並建立 Hosting 網站。
2. 建立 Firestore `(default)`（Native mode），發布 `firestore.rules`（全部拒絕），套用 `firestore.indexes.json` 的索引豁免。
3. 建立 Artifact Registry，設定清理規則：保留最新 3 個、刪除 7 天前的映像。
4. 建立兩個 service account，只給最小權限：

   | SA | 權限 |
   |---|---|
   | `nthu-chatbot`（執行期） | `datastore.user`；三個 Secret **個別**授予 `secretAccessor` |
   | `nthu-chatbot-deployer`（Cloud Build） | `run.developer`、`logging.logWriter`、只限該 AR repo 的 `artifactregistry.writer`、只能代理執行期 SA 的 `iam.serviceAccountUser` |

5. GitHub Actions 部署前端用的 Workload Identity Federation：provider 只信任 `GITHUB_OWNER/GITHUB_REPO` 在 `DEPLOY_REF` 上的 workflow，換到的身分是只有 `firebasehosting.admin` 的 `nthu-chatbot-hosting` SA；不產生任何金鑰。
6. 建立 Secret（只建容器，不碰值）；任何 Secret 還沒有值時會停下來。
7. 建立或更新 Cloud Run 服務：環境變數（含 `CORS_ALLOWED_ORIGINS` = Hosting 的兩個預設網域）、Secret 掛載、`--timeout=180 --concurrency=40 --cpu-boost`、`min-instances=0`，並開放公開呼叫（webhook 與 API 自己驗證簽章和 id_token）。
8. 建立 Cloud Build trigger（`deploy-<service>`），使用 `cloudbuild.yaml` 和部署 SA。
9. 最後印出要設定的 GitHub repo variables（`GCP_WIF_PROVIDER_<ENV>`、`GCP_HOSTING_SA_<ENV>`，不是機密）。設定後 CI 才會部署前端，沒設定時該 job 會略過。

### `monitoring.sh` 做的事

- Email 通知管道（`ALERT_EMAIL` 只從環境變數讀，不會寫進 repo）。
- Log-based metric：該服務 `severity>=ERROR` 的 log。Cloud Run 上 log 是帶 severity 的 JSON，見 `log.py`。
- 告警：5 分鐘內超過 5 個 5xx、出現 ERROR log。
- **防冷啟動與存活檢查預設交給外部 ping 服務**：設定成每 5–10 分鐘 `GET <API 網址>/ping`、檢查回應含 `pong`，或讓 UptimeRobot 使用 `HEAD <API 網址>/ping`、檢查 HTTP 狀態碼為 `200`（HEAD 無 body，不做關鍵字檢查）。Cloud Run 閒置約 15 分鐘後回收實例，間隔不要超過 10 分鐘。`/ping` 不碰資料庫也不呼叫 OpenAI。
- 只有帶 `UPTIME_CHECK=true` 時才另外建 GCP uptime check（每 5 分鐘、每月前 100 萬次執行免費）與「uptime 失敗」告警。

## 加入 Secret 值

值只從 stdin 進 Secret Manager，不會出現在指令列、shell history 或檔案裡：

```bash
# SUFFIX：staging 留空，prod 用 -prod
SUFFIX=-prod
read -rsp "openai-api-key: " v && printf %s "$v" | gcloud secrets versions add openai-api-key$SUFFIX --project=nthusa-chatbot --data-file=- ; unset v
read -rsp "line-channel-secret: " v && printf %s "$v" | gcloud secrets versions add line-channel-secret$SUFFIX --project=nthusa-chatbot --data-file=- ; unset v
read -rsp "line-channel-access-token: " v && printf %s "$v" | gcloud secrets versions add line-channel-access-token$SUFFIX --project=nthusa-chatbot --data-file=- ; unset v
```

這幾行要在**自己的終端機**執行（需要互動輸入）。Git Bash 請用 **Shift+Insert** 或右鍵貼上：Ctrl+V 只會輸入一個看不見的控制字元。輪替金鑰時加新版本，再把舊版本 disable：

```bash
gcloud secrets versions disable <舊版本號> --secret=<name> --project=<PROJECT_ID>
```

## 建立 prod

1. `prod.conf` 已填好（與 staging 同一個專案，資源分開命名）。
2. 在 LINE Developers 的**正式 Provider** 建立 Messaging API 與 LINE Login channel；LINE Login 底下建立 LIFF app。記下 channel ID 與 LIFF ID（不寫進 repo，見「LINE 頻道設定」）。
3. Cloud Build 已連結 `NTHU-SA/NTHU-Chatbot`（staging 用的同一個連結）。
4. `LINE_LOGIN_CHANNEL_ID=<正式 Login channel ID> LIFF_ID=<正式 LIFF ID> bash infra/bootstrap.sh infra/environments/prod.conf`：第一次會停在 Secret 沒有值的那一步。
5. 依上一節加入三個 Secret 值，再跑一次 bootstrap（同樣帶上兩個 LINE ID）。
6. `ALERT_EMAIL=... bash infra/monitoring.sh infra/environments/prod.conf`，並把外部 ping 服務指向 prod 的 `/ping`
7. 確認 bootstrap 印出的服務網址與 `prod.conf` 的 `API_ORIGIN` 相同；在 GitHub repo 設定印出的兩個 variables（`..._PROD`），以及 `LIFF_ID_PROD`。
8. LINE Developers：Webhook URL 設為 `<服務網址>/callback`、開啟 Use webhook；LIFF Endpoint URL 設為 `https://nthusa-chatbot-prod.web.app/`；用正式 bot 的 token 部署 rich menu（`uv run python -m scripts.rich_menu`）。
9. 把 `main` 合併一次，觸發 API 與前端的第一次部署，再用 `/ping` 和 LINE 實測。

## 把 staging 校正到同一套設定

staging 是在這套腳本之前手動建的。跑一次 `bootstrap.sh` 會補上部署 SA 與新 trigger（`deploy-nthu-chatbot-staging`），服務設定也會被校正成和 prod 相同。
確認新 trigger 能成功部署後，再手動清掉舊設定：

```bash
# 舊 trigger（部署 fork 的 feat/liff-chat）
gcloud builds triggers delete rmgpgab-nthu-chatbot-staging-asia-northeast1-ChiuKuanHsun-NTrrv --project=nthusa-chatbot
# 舊 trigger 用的 default compute SA 權限過大（專案層級 run.admin 與 serviceAccountUser）
for role in roles/run.admin roles/run.builder roles/iam.serviceAccountUser roles/artifactregistry.writer roles/logging.logWriter; do
  gcloud projects remove-iam-policy-binding nthusa-chatbot \
    --member=serviceAccount:1008940193124-compute@developer.gserviceaccount.com --role="$role" --condition=None
done
```

## 驗證

```bash
curl -fsS <服務網址>/ping                       # {"message":"pong"}
curl -fsS https://<HOSTING_SITE>.web.app/config.json   # 只有 liffId 與 apiBase
curl -sI -X OPTIONS <服務網址>/api/sessions -H "Origin: https://evil.example" -H "Access-Control-Request-Method: GET"   # 不應有 access-control-allow-origin
gcloud run services describe <service> --region=<region> --project=<PROJECT_ID> \
  --format="yaml(spec.template.spec.serviceAccountName,spec.template.metadata.annotations)"
```

告警測試：暫時把 uptime check 的路徑改成不存在的頁面（例如 `/nope`），幾分鐘內應該會收到告警信；測完改回 `/ping`。
