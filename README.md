# NTHU-LINE-Bot

清華校園 LINE Bot「狗狗情報員」，使用 FastAPI、LINE Messaging API、LIFF、OpenAI Agents SDK、NTHU Data MCP 與 Firebase Firestore。
應用部署於 Cloud Run，不需要叢集或常駐背景 worker。

## 架構

```text
LINE 聊天室 ──webhook──▶ /callback ──▶ @ 指令模組（公車/餐廳/圖書館/地圖/公告…）→ NTHU REST API v2
                                   └─ 其他文字 → 回一則按鈕，開啟 LIFF 並帶入問題（按鈕綁定該對話，重新點擊回到同一串對話）

LIFF 網頁 (/liff/) ──Bearer id_token──▶ /api/* ──▶ AgentRunner（OpenAI Agents SDK）
                                                 ├─ MCP: https://api.nthusa.tw/mcp（9 個唯讀工具）
                                                 └─ Firestore: users/{uid}/sessions/{sid}/messages
                       ◀── SSE: user_message / tool_call_start / tool_call_end / token / done / error
```

- **聊天室只做「reply token 一定來得及」的事**；AI 對話在 LIFF 網頁進行（多 session、串流、即時顯示正在使用哪個工具）。
- 身分：LIFF `id_token` → 後端向 `https://api.line.me/oauth2/v2.1/verify` 驗證 → `sub` 即 userId。Messaging API 與 LINE Login channel 必須在同一 Provider 下，userId 才相同。
- LLM 可接任何 OpenAI 相容端點（`OPENAI_BASE_URL`），預設 `gpt-4.1-mini`；MCP 工具由後端呼叫，白名單 `MCP_ALLOWED_TOOLS`。
- 群組內只使用 `@` 指令；其他文字會回不帶問題的 LIFF 按鈕，不讀取私人對話。
- 回呼等待處理完成才回應，沒有會在 Cloud Run 回應後被暫停的背景工作。
- 啟動時不建立 Rich Menu；更新選單請另外執行 `python -m scripts.rich_menu`。

Rich Menu 更新會取代現有選單，執行前請確認 `data/richmenu/menu-main.png`、`data/richmenu/menu-more.png` 均存在且正確。

## 目錄

```text
main.py / log.py                  啟動腳本、loguru 攔截
bot_config.yaml                   @ 指令模組與中文前綴對照
src/core/config.py                Settings（全部從環境變數讀取）
src/app/__init__.py               create_app()、lifespan 組裝 app.state
src/app/routes/callback.py        LINE webhook
src/app/routes/chat.py            /api/*（LIFF 對話，SSE）
src/app/security.py               id_token 驗證、限流
src/app/middleware.py             安全標頭與 CSP
src/app/static/liff/              LIFF 前端（原生 JS）
src/app/handlers/、src/modules/   聊天室內 @ 指令
src/application/                  資料模型、ChatStore 介面與記憶體實作
src/infrastructure/ai/            AgentRunner、狗狗情報員 prompt
src/infrastructure/firebase/      FirestoreChatStore
templates/messages/               Flex 訊息（含 LIFF 入口泡泡）
tests/                            pytest（unittest 風格）
```

## 機密與設定

**所有憑證只從環境變數讀取，任何檔案都不得含真實值。** 本機使用 git-ignored 的 `.env`（由 `.env.template` 複製）；
正式環境由 Secret Manager 注入。`.gitignore`、`.dockerignore`、`.gcloudignore` 均排除 `.env*` 與金鑰檔。

| 類別 | 變數 |
| --- | --- |
| 機密（Secret Manager） | `LINE_CHANNEL_SECRET`、`LINE_CHANNEL_ACCESS_TOKEN`、`OPENAI_API_KEY` |
| LINE Login / LIFF | `LINE_LOGIN_CHANNEL_ID`、`LIFF_ID` |
| LLM | `OPENAI_MODEL`、`OPENAI_BASE_URL`（選填）、`OPENAI_USE_RESPONSES_API`（選填） |
| MCP | `MCP_SERVER_URL`、`MCP_ALLOWED_TOOLS`（逗號分隔，選填）、`MCP_TIMEOUT_SECONDS` |
| 儲存 | `CHAT_STORE`（`firestore` / `memory`）、`GOOGLE_CLOUD_PROJECT` |
| 限制（選填） | `HISTORY_WINDOW`、`HISTORY_MESSAGE_CHARS`、`MAX_TOOL_OUTPUT_CHARS`、`MAX_MESSAGE_CHARS`、`DAILY_MESSAGE_LIMIT`、`MAX_AGENT_TURNS`、`TOOL_RESULT_PREVIEW_CHARS` |

Firestore 憑證不是環境變數：Cloud Run 用執行服務帳號的 ADC，本機用 `gcloud auth application-default login`。

## LINE Developers 設定

同一個 **Provider** 底下建立兩個 channel：

1. **Messaging API channel**：Channel secret → `LINE_CHANNEL_SECRET`；長期 Channel access token → `LINE_CHANNEL_ACCESS_TOKEN`。
   Webhook URL 設為 `https://YOUR_SERVICE_URL/callback`，啟用 Webhook，停用衝突的自動回應與歡迎訊息。
2. **LINE Login channel**：Channel ID → `LINE_LOGIN_CHANNEL_ID`。新增 **LIFF app**：Endpoint URL `https://YOUR_SERVICE_URL/liff/`、Size `Full`、
   Scopes 勾選 `profile` 與 **`openid`**（缺少就拿不到 id_token）。LIFF ID → `LIFF_ID`。
   在 LINE 以外的瀏覽器測試時，需把自己加入 Login channel 的 tester 或發佈 channel。

## 本機開發

需要 Python 3.12+。以下為 PowerShell 指令：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.template .env
```

在 `.env` 填入 LINE、LIFF、LLM 設定。不要覆蓋已有的 `.env`。
沒有 GCP 憑證時設 `CHAT_STORE=memory`（對話不會保存）；要用正式 Firestore 則：

```powershell
gcloud auth application-default login
.\.venv\Scripts\python.exe main.py
```

預設網址為 `http://localhost:5000`，健康檢查為 `/ping`，`GET /api/config` 應只回傳 `liff_id`。
LINE 需要公開 HTTPS 網址：`ngrok http 5000` 後，把 ngrok 網址填到 Webhook URL（`/callback`）與 LIFF Endpoint URL（`/liff/`）。

## Firebase 資料

在 Firebase Console 建立專案及 **Firestore Native mode `(default)`** 資料庫。
建議與 Cloud Run 使用相近區域；資料庫位置建立後不能任意更換。

| 路徑 | 資料 |
| --- | --- |
| `users/{uid}` | `last_seen_at`、`followed`（僅 follow/unfollow 時變更）、`display_name`（LIFF 登入時寫入）、`created_at`、`usage.{YYYY-MM-DD}`（每日 LLM 訊息計數） |
| `users/{uid}/sessions/{sid}` | `title`、`created_at`、`updated_at`、`message_count`、`origin`（LINE 泡泡的 webhook event id，同一顆按鈕永遠開同一個對話）；每位使用者最多保留 50 個，超過時最久未更新的對話會被自動刪除 |
| `users/{uid}/sessions/{sid}/messages/{mid}` | `role`、`content`、`created_at`、`tool_calls` |

`uid` 是 LINE userId，webhook 與 LIFF 共用同一份 `users/{uid}`。若需刪除個人資料，管理者以 `recursive_delete` 移除 `users/{uid}` 及其子集合。
資料沒有自動到期；上線前應公告資料用途、保存期限與刪除聯絡方式。

Firestore 規則拒絕所有用戶端直接存取；後端透過服務帳號 IAM 存取，不依賴 Firebase Auth。
部署規則與索引排除：

```powershell
firebase deploy --only firestore --project YOUR_PROJECT_ID
```

注意：此命令會套用專案的規則，若與其他應用共用 Firebase，先合併規則再部署。
舊版的 `conversations` collection 已不再使用，請在 Console 手動刪除後再部署（移除 TTL 設定後殘留文件不會自動清除）。

## Cloud Run 部署

需要啟用計費的 Firebase/GCP 專案、Google Cloud CLI 及 Firebase CLI。
建置使用 Cloud Build / Artifact Registry，金鑰使用 Secret Manager。
部署者需 Cloud Run 管理與服務帳號使用權限；建置服務帳號需 `roles/run.builder`。
請參考 [Cloud Run source deployment prerequisites](https://cloud.google.com/run/docs/deploying-source-code)。

1. 啟用 API 並建立專用執行服務帳號（只需執行一次）：

```powershell
$ProjectId = "YOUR_PROJECT_ID"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com firestore.googleapis.com secretmanager.googleapis.com --project $ProjectId
gcloud iam service-accounts create nthu-chatbot --project $ProjectId
$RuntimeAccount = "nthu-chatbot@$ProjectId.iam.gserviceaccount.com"
gcloud projects add-iam-policy-binding $ProjectId --member "serviceAccount:$RuntimeAccount" --role roles/datastore.user
```

2. 在 Secret Manager Console 建立 `openai-api-key`、`line-channel-secret`、`line-channel-access-token`，各加入一個啟用版本。不要把金鑰貼進聊天或提交至 Git。

```powershell
foreach ($Secret in @("openai-api-key", "line-channel-secret", "line-channel-access-token")) {
    gcloud secrets add-iam-policy-binding $Secret --project $ProjectId --member "serviceAccount:$RuntimeAccount" --role roles/secretmanager.secretAccessor
}
```

3. 部署 Firestore 設定，再以 repo 根目錄首次部署（之後可改由 GitHub 持續部署）：

```powershell
firebase deploy --only firestore --project $ProjectId
gcloud run deploy nthu-chatbot --source . --project $ProjectId --region asia-east1 `
  --service-account $RuntimeAccount --allow-unauthenticated `
  --timeout=180 --concurrency=40 --min-instances=0 --max-instances=3 `
  --set-secrets "OPENAI_API_KEY=openai-api-key:latest,LINE_CHANNEL_SECRET=line-channel-secret:latest,LINE_CHANNEL_ACCESS_TOKEN=line-channel-access-token:latest" `
  --set-env-vars "LINE_LOGIN_CHANNEL_ID=YOUR_LOGIN_CHANNEL_ID,LIFF_ID=YOUR_LIFF_ID,OPENAI_MODEL=gpt-4.1-mini,MCP_SERVER_URL=https://api.nthusa.tw/mcp,CHAT_STORE=firestore,GOOGLE_CLOUD_PROJECT=$ProjectId"
```

`--timeout=180` 需大於 LIFF 對話的 120 秒 agent 上限（SSE 長連線）；使用非官方端點時再加 `OPENAI_BASE_URL`。
執行服務帳號透過 ADC 存取 Firestore，不上傳 JSON 私鑰。`.gcloudignore` 與 `.dockerignore` 會排除本機金鑰、環境檔與虛擬環境。
不得在 Cloud Run 設定 `FIRESTORE_EMULATOR_HOST`。更新 Secret 後需重新部署新 revision。

4. **從 GitHub 持續部署**：在 Cloud Run Console 對此服務設定「Continuous deployment from repo」，連結 GitHub repo 並選擇以 Dockerfile 建置。
   機密與環境變數設定在 service 層級（上一步），之後每次 push 產生的 revision 會自動繼承，GitHub 端不需要任何 GCP 金鑰。
   若改用 GitHub Actions，只能使用 Workload Identity Federation，不得把服務帳號 JSON 存進 GitHub Secrets。

5. 將部署輸出的 HTTPS 網址填入 LINE Webhook（`/callback`）與 LIFF Endpoint URL（`/liff/`），驗證 `/ping`、`/api/config`、LINE Verify、指令查詢，
   以及在聊天室輸入問題 → 按鈕開啟 LIFF → 串流回覆。

LIFF 對話單次最多 `MAX_AGENT_TURNS` 回合、120 秒逾時、每人每日 `DAILY_MESSAGE_LIMIT` 則；單一實例另有突發限流。
`RateLimiter`、id_token 快取與 MCP 連線都是單一實例狀態；每日額度存在 Firestore，跨實例仍正確。
建議設定 Google Cloud 與 LLM 供應商的預算告警，並依流量調整實例上限。

## API v2 遷移

依據 [最新 OpenAPI](https://api.nthusa.tw/openapi.json)（`info.version=2.0.0`）調整：

- 公車：`/buses/info/stops` 使用 `name`；`/buses/stops/{stop_name}` 使用扁平到站欄位，支援 null/空班次。
- 餐廳：`/dining/` 為建築與餐廳巢狀資料；週末查詢改用 `/dining/open?schedule=...`。
- 圖書館：`/libraries/space` 保留剩餘數量 0；`/libraries/rss/{rss_type}` 處理空頁及 nullable 連結。
- 地圖：使用 `/locations/search?query=...` 模糊搜尋。
- 公告：`/announcements/` 使用 department/language 篩選；新版 `title` 是文章標題，舊模組的佈告欄名稱改在本地篩選。移除 coroutine 快取，處理 nullable 文章欄位。

舊版程式曾包含硬編碼 Gemini 金鑰。移除檔案不會撤銷金鑰，也不會移除 Git 歷史；請在供應商 Console 撤銷或輪替。
LLM 供應商會接收最近 `HISTORY_WINDOW` 則對話，且有其自身的資料政策；MCP 會接收模型產生的查詢參數。Agents SDK 的 tracing 已停用，不會把對話送到 OpenAI 追蹤後端。

## 測試

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

純單元測試不使用雲端金鑰或付費模型（LLM、LINE 驗證與 Firestore 皆以假物件或記憶體實作取代），Firestore 整合測試沒有 emulator 時會跳過。
安裝 Firebase CLI 與 Java 21+ 後可跑完整測試：

```powershell
firebase emulators:exec --only firestore --project demo-nthu-chatbot ".venv\Scripts\python.exe -m pytest -q"
```

本機使用 emulator 開發時，在另一個終端執行 `firebase emulators:start --only firestore --project demo-nthu-chatbot`，
並將應用的 `GOOGLE_CLOUD_PROJECT=demo-nthu-chatbot`、`FIRESTORE_EMULATOR_HOST=127.0.0.1:8085` 設為一致。

## Contributing
### Commit Messages:
#### Types:
- feat: Add or update a feature.
- fix: Fix a bug.
- docs: Changes to documentation.
- style: Changes that do not affect the meaning of the code (white-space, formatting, missing semi colons, etc).
- refactor: Code change that neither fixes a bug nor adds a feature.
- perf: Code change that improves performance.
- test: Adding missing tests.
- chore: Changes to the build process or auxiliary tools and libraries.
- revert: Revert to a previous commit. Format: "revert: type(scope): subject (version: xxxx)".