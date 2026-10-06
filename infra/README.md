# 部署與維運（staging / prod）

[回到 README](../README.md) · [設定指南](../docs/configuration.md) · [分支與發版](../docs/development.md#分支與發版)

兩個環境放在**同一個 GCP 專案**（`nthusa-chatbot`），但所有資源都分開命名，權限也只授予自己那一份：

| | staging | prod |
|---|---|---|
| 設定檔 | `infra/environments/staging.conf` | `infra/environments/prod.conf` |
| GCP 專案 | `nthusa-chatbot` | `nthusa-chatbot`（同一個） |
| Firestore 資料庫 | `(default)` | `prod` |
| Secret | `openai-api-key`、`line-channel-secret`、`line-channel-access-token` | 同名加 `-prod` |
| Service accounts | `nthu-chatbot`、`nthu-chatbot-deployer`、`nthu-chatbot-hosting` | `nthu-chatbot-prod`、`nthu-chatbot-prod-deployer`、`nthu-chatbot-prod-hosting` |
| WIF pool（GitHub Actions 部署前端） | `github-actions`（只信任 `refs/heads/main`） | `github-actions-prod`（只信任 `refs/tags/v*`） |
| 部署來源 | `NTHU-SA/NTHU-Chatbot` 合併進 `main` | `NTHU-SA/NTHU-Chatbot` 的版本 tag `vX.Y.Z` |
| Cloud Build | `cloudbuild.yaml`（建置映像） | `cloudbuild.release.yaml`（沿用 staging 同一個 commit 的映像，不重新建置） |
| Cloud Run | `nthu-chatbot-staging` | `nthu-chatbot` |
| LINE | 測試用 Provider 的頻道 | 正式 Provider（Messaging API 與 LINE Login 必須在**同一個 Provider**） |
| Firestore 刪除保護 | 關 | 開 |
| 前端（Firebase Hosting） | `nthusa-chatbot-staging.web.app` | `chat.nthusa.tw`（自訂網域；`nthusa-chatbot-prod.web.app` 也能連） |
| Auth0（一般瀏覽器登入） | 學生會 tenant（`auth.nthusa.tw`）的 Application「Chat (staging)」，API `https://chat.nthusa.tw/api/staging` | 同一個 tenant 的「Chat (prod)」，API `https://chat.nthusa.tw/api` |

權限隔離：
- 執行期 SA 的 `roles/datastore.user` 帶 IAM condition，只能存取自己的 Firestore 資料庫；Secret 逐一授權。
- 部署 SA 的 `roles/run.developer` 只授予自己的 Cloud Run 服務（不是整個專案），只能代理自己的執行期 SA。
- WIF 每個環境一個 pool：principalSet 是以 pool 為範圍，共用 pool 時 `main` 的 token 也能代理 prod 的 SA。
- 仍共用的部分：帳單、專案層級的 Owner / Editor、Artifact Registry repo（映像依服務分開路徑；prod 部署 SA 從 staging 的路徑讀取映像），以及 Hosting 部署 SA 的 `firebasehosting.admin`（專案層級，靠 branch protection、tag ruleset 與 WIF 的 ref 限制保護）。正式資料的存取請只給少數維運帳號。

staging 用 `cloudbuild.yaml` 建置；prod 用 `cloudbuild.release.yaml`：檢查 tag 等於 `pyproject.toml` 的 version，把 `nthu-chatbot-staging:<commit>` 複製成 `nthu-chatbot:<tag>`，以 digest 部署。staging 沒建過這個 commit（不在 `main` 上）時找不到映像而失敗。
前端由 CI（`.github/workflows/ci.yml` 的 `deploy-frontend`）部署到 Firebase Hosting；`infra/build_frontend.py` 依環境設定檔產生 `config.json`（只有公開的 LIFF ID、API 網址與 Auth0 設定）和 CSP（`connect-src` 只允許該環境的 API 與 Auth0 網域）。

## 部署流程

- **API**：合併進 `main` → Cloud Build（`cloudbuild.yaml`，專用的最小權限部署 SA）建置映像 → 只更新 staging 的 Cloud Run 映像。推送版本 tag → `cloudbuild.release.yaml` 提升映像並部署 prod。環境變數與 Secret 設定在服務上，每個 revision 自動沿用；`--timeout=180` 必須大於 120 秒的 agent 上限。
- **前端**：push 到 `main`（staging）或版本 tag（prod）且測試與容器 smoke test 通過後，CI 以 Workload Identity Federation 部署到 Hosting；版本 tag 同樣先檢查版本號與是否在 `main` 上。手動部署在 repo 根目錄用 Git Bash 執行 `bash infra/deploy_frontend.sh infra/environments/<env>.conf`。
- **監測**：Cloud Run 上的 log 是帶 `severity` 的 JSON，可用 `severity>=ERROR` 篩選；`infra/monitoring.sh` 建立 5xx 與 ERROR log 告警。外部 ping 與選用 GCP uptime check 的設定見下方「`monitoring.sh` 做的事」。

發版、tag 與回滾步驟見[開發指南](../docs/development.md#分支與發版)；
首次建置環境則依下方的頻道設定、腳本與「建立 prod」操作。

## LINE 頻道設定（不寫在 repo）

LINE 的 ID 和金鑰都依環境注入，repo 裡的設定檔不含任何 LINE 頻道資訊：

| 值 | 放在哪裡 | 怎麼設定 |
|---|---|---|
| `LINE_LOGIN_CHANNEL_ID`、`LIFF_ID`（後端） | 該環境 Cloud Run 的環境變數 | 第一次建立或更換頻道時：`LINE_LOGIN_CHANNEL_ID=... LIFF_ID=... bash infra/bootstrap.sh infra/environments/<env>.conf`；之後重跑 bootstrap 不帶這兩個變數會沿用服務上的值 |
| `LIFF_ID`（前端 `config.json`） | GitHub repo variable `LIFF_ID_STAGING` / `LIFF_ID_PROD` | `gh variable set LIFF_ID_<ENV> -R NTHU-SA/NTHU-Chatbot --body <LIFF ID>`；沒設定時 CI 跳過前端部署。手動部署（`deploy_frontend.sh`）沒給 `LIFF_ID` 時讀 Cloud Run 上的值 |
| channel secret、access token | Secret Manager（prod 的名稱加 `-prod`） | 用 stdin 加入（見下方「加入 Secret 值」） |

更換頻道時後端與前端要一起換，否則 LIFF 的 id_token 會因 channel ID 不符被拒絕。

## Auth0（一般瀏覽器登入）

LINE 裡（LIFF）仍用 LINE Login；用一般瀏覽器開啟時改走 Auth0 Universal Login。chat 使用學生會的 Auth0 tenant（custom domain `auth.nthusa.tw`），staging / prod 各自一組 Application 與 API，audience 不同，staging 的 token 不能呼叫 prod 的 API。

| 值 | 放在哪裡 |
|---|---|
| `AUTH0_DOMAIN`、`AUTH0_AUDIENCE` | 環境設定檔（公開值） |
| `AUTH0_CLIENT_ID`（後端） | Cloud Run 環境變數：`AUTH0_CLIENT_ID=... bash infra/bootstrap.sh infra/environments/<env>.conf`；之後重跑會沿用服務上的值 |
| `AUTH0_CLIENT_ID`（前端 `config.json`） | GitHub repo variable `AUTH0_CLIENT_ID_STAGING` / `AUTH0_CLIENT_ID_PROD` |

三個值缺一時 Auth0 不啟用，一般瀏覽器沿用 LIFF 的 LINE 網頁登入，所以可以先部署程式、再建立 Auth0 設定。

Auth0 Dashboard（每個環境各一次）：

1. **APIs → Create API**：Identifier 填該環境的 `AUTH0_AUDIENCE`（建立後不能改）、Signing Algorithm RS256；Settings 開啟 **Allow Offline Access**（refresh token）。
2. **Applications → Create Application → Single Page Application**，名稱「Chat (staging)」/「Chat (prod)」：
   - Allowed Callback URLs、Allowed Logout URLs：前端網址（staging `https://nthusa-chatbot-staging.web.app/`、prod `https://chat.nthusa.tw/`；bootstrap 最後會印出）
   - Allowed Web Origins：同上但不含結尾斜線
   - Refresh Token Rotation 開啟、設定 Absolute Lifetime；Grant Types 只留 Authorization Code 與 Refresh Token
   - Connections 分頁只開要給 chat 用的登入方式
3. 把 Application 的 Client ID 依上表設定到 Cloud Run 與 repo variable。

和學生會中央驗證共用同一個 tenant，請注意：
- post-login Action（例如 allowlist）會套用到 tenant 裡所有 Application，必須以 `event.client.client_id` 限定範圍，否則會擋住 chat 的使用者。
- 不要對 chat 的使用者做 Auth0 帳號連結：連結後被併入的身分 `sub` 會改成主帳號的，chat 會把他當成另一個人。chat 的帳號連結在後端做（內部 user id）。
- 目前 Auth0 登入（provider `auth0`）與 LIFF 登入（provider `line`）是**不同的內部 user**，對話不互通。之後要讓兩者對到同一人（後續 PR），前提是 LINE connection 的 LINE Login channel 和 bot 在同一個 LINE Provider，兩邊的 LINE user ID 才會相同。
- 隱私權政策（第 3 版起）已說明以 Google 經 Auth0 登入時的資料；之後新增其他登入方式（例如 GitHub）時，要先更新 `privacy.html` 並遞增 `PRIVACY_POLICY_VERSION`。

## 網域

- staging：Firebase Hosting 網站 `nthusa-chatbot-staging`（site ID 不能改名，所以另建網站）。舊網站 `nthusa-chatbot` 在 DEV LIFF 的 Endpoint URL 改過去、確認可用後再刪除：`curl -X DELETE -H "Authorization: Bearer $(gcloud auth print-access-token)" -H "x-goog-user-project: nthusa-chatbot" https://firebasehosting.googleapis.com/v1beta1/projects/nthusa-chatbot/sites/nthusa-chatbot`
- prod：`chat.nthusa.tw` 是 `nthusa-chatbot-prod` 網站的自訂網域（`prod.conf` 的 `CUSTOM_DOMAIN`，bootstrap 會把它加進 CORS）。DNS 在 Cloudflare，照 Firebase Console → Hosting → 自訂網域顯示的記錄新增，並設為 **DNS only（灰雲）**，否則 Firebase 簽不到憑證。網域生效後把正式 LIFF 的 Endpoint URL 改成 `https://chat.nthusa.tw/`。

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
3. 建立 Artifact Registry，設定清理規則：版本 tag（`v` 開頭）的映像永久保留（回滾用），其餘保留最新 10 個、超過 7 天刪除。
4. 建立兩個 service account，只給最小權限：

   | SA | 權限 |
   |---|---|
   | `nthu-chatbot`（執行期） | `datastore.user`；三個 Secret **個別**授予 `secretAccessor` |
   | `nthu-chatbot-deployer`（Cloud Build） | `run.developer`、`logging.logWriter`、只限該 AR repo 的 `artifactregistry.writer`、只能代理執行期 SA 的 `iam.serviceAccountUser` |

5. GitHub Actions 部署前端用的 Workload Identity Federation：provider 只信任 `GITHUB_OWNER/GITHUB_REPO` 在 `DEPLOY_REF` 上的 workflow（結尾 `*` 為前綴比對，prod 是 `refs/tags/v*`），換到的身分是只有 `firebasehosting.admin` 的 `nthu-chatbot-hosting` SA；不產生任何金鑰。
6. 建立 Secret（只建容器，不碰值）；任何 Secret 還沒有值時會停下來。
7. 建立或更新 Cloud Run 服務：環境變數（含 `CORS_ALLOWED_ORIGINS` = Hosting 的兩個預設網域）、Secret 掛載、`--timeout=180 --concurrency=40 --cpu-boost`、`min-instances=0`，並開放公開呼叫（webhook 與 API 自己驗證簽章和 id_token）。
8. 建立或校正 Cloud Build trigger（`deploy-<service>`）：conf 的 `BRANCH_REGEX`（staging）或 `TAG_REGEX`（prod）擇一、`BUILD_CONFIG`（預設 `cloudbuild.yaml`）、substitutions，使用部署 SA。
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
8. LINE Developers：Webhook URL 設為 `<服務網址>/callback`、開啟 Use webhook；LIFF Endpoint URL 設為 `https://chat.nthusa.tw/`（網域生效前用 `https://nthusa-chatbot-prod.web.app/`）；用正式 bot 的 token 部署 rich menu（`uv run python -m scripts.rich_menu`）。
9. 依[分支與發版](../docs/development.md#分支與發版)打第一個版本 tag，觸發 API 與前端的第一次部署，再用 `/ping` 和 LINE 實測。

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

部署後把 API 網址填進 LINE Webhook、Hosting 網址填進 LIFF Endpoint URL，
再驗證 `/ping`、LINE Verify、指令查詢，以及聊天室提問 → 開啟 LIFF → 串流回覆。
更新 Secret 後要部署新 revision 才會生效；不得在 Cloud Run 設定 `FIRESTORE_EMULATOR_HOST`。
建議設定 GCP 與 LLM 供應商的預算告警。

```bash
curl -fsS <服務網址>/ping                       # {"message":"pong"}
curl -fsS https://<HOSTING_SITE>.web.app/config.json   # 只有 liffId、apiBase、privacyPolicyVersion 與 auth0（domain / clientId / audience）
curl -sI -X OPTIONS <服務網址>/api/sessions -H "Origin: https://evil.example" -H "Access-Control-Request-Method: GET"   # 不應有 access-control-allow-origin
gcloud run services describe <service> --region=<region> --project=<PROJECT_ID> \
  --format="yaml(spec.template.spec.serviceAccountName,spec.template.metadata.annotations)"
```

告警測試：暫時把 uptime check 的路徑改成不存在的頁面（例如 `/nope`），幾分鐘內應該會收到告警信；測完改回 `/ping`。
