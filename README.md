# NTHU-LINE-Bot

清華校園 LINE Bot，使用 FastAPI、OpenAI Responses API、NTHU MCP 與 Firebase Firestore。
應用部署於 Cloud Run，不需要叢集或常駐背景 worker。

## 架構

```text
LINE -> Cloud Run /callback -> 簽章驗證 -> 指令模組 -> NTHU REST API v2
                                       -> ChatService -> OpenAI -> NTHU MCP
                                       -> Firestore users / conversations
```

- AI 使用 `https://api.nthusa.tw/mcp`，僅允許 9 個已確認的唯讀校務工具。
- OpenAI SDK 直接呼叫 Responses API，預設模型 `gpt-4.1-mini`，可用 `OPENAI_MODEL` 調整。
- 一對一聊天支援 AI 與 `reset`；群組只使用既有指令，不讀取私人對話。
- 回呼等待處理完成才回應，沒有會在 Cloud Run 回應後被暫停的背景工作。
- 啟動時不建立 Rich Menu；更新選單請另外執行 `python -m scripts.rich_menu`。

Rich Menu 更新會取代現有選單，執行前請確認 `data/richmenu/menu-main.png`、`data/richmenu/menu-more.png` 均存在且正確。

## 本機開發

需要 Python 3.13+。以下為 PowerShell 指令：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.template .env
```

在 `.env` 填入 LINE、OpenAI 金鑰及 `GOOGLE_CLOUD_PROJECT`。不要覆蓋已有的 `.env`。
正式 Firebase 資料庫使用 Application Default Credentials：

```powershell
gcloud auth application-default login
.\.venv\Scripts\python.exe main.py
```

預設網址為 `http://localhost:5000`，健康檢查為 `/ping`。
LINE 需要公開 HTTPS 網址，設定 Webhook URL 為 `https://YOUR_SERVICE_URL/callback`。
在 LINE Developers Console 啟用 Webhook，停用衝突的自動回應與歡迎訊息。

## Firebase 資料

在 Firebase Console 建立專案及 **Firestore Native mode `(default)`** 資料庫。
建議與 Cloud Run 使用相近區域；資料庫位置建立後不能任意更換。

| Collection | Document ID | 資料 |
| --- | --- | --- |
| `users` | LINE user ID | 最近互動時間、追蹤狀態（僅 follow/unfollow 時變更） |
| `conversations` | LINE user ID | 最近 10 輪文字、最近事件回覆、90 秒交易鎖、到期時間 |

對話在最近互動 30 天後過期，程式不再使用過期歷史；TTL 清除是非即時的。
`reset` 清除歷史，但保留基本使用者資料。若需刪除個人資料，管理者須刪除兩個 collection 中該 user ID 的文件。
基本使用者資料沒有自動到期；上線前應公告資料用途、保存期限與刪除聯絡方式。

Firestore 規則拒絕所有用戶端直接存取；後端透過服務帳號 IAM 存取，不依賴 Firebase Auth。
部署規則、索引排除與對話 TTL：

```powershell
firebase deploy --only firestore --project YOUR_PROJECT_ID
```

注意：此命令會套用專案的規則，若與其他應用共用 Firebase，先合併規則再部署。
在 Console 確認 `conversations.expires_at` 的 TTL 已啟用。

## Cloud Run 部署

需要啟用計費的 Firebase/GCP 專案、Google Cloud CLI 及 Firebase CLI。
除 Cloud Run 與 Firestore 外，建置使用 Cloud Build / Artifact Registry，金鑰使用 Secret Manager。
部署者需 Cloud Run 管理與服務帳號使用權限；source build 的建置服務帳號需 `roles/run.builder`。
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

3. 部署 Firestore 設定後，再部署應用：

```powershell
firebase deploy --only firestore --project $ProjectId
.\scripts\deploy.ps1 -ProjectId $ProjectId
```

腳本使用 repo 根目錄建置，預設 `asia-east1`、8080 port、60 秒 request timeout、0 到 3 個實例。
Webhook 必須公開可達，真正的事件授權由 LINE HMAC 簽章提供。
執行服務帳號透過 ADC 存取 Firestore，不上傳 JSON 私鑰。
`.gcloudignore` 與 `.dockerignore` 會排除本機金鑰、環境檔與虛擬環境。
不得在 Cloud Run 設定 `FIRESTORE_EMULATOR_HOST`。更新 Secret 後需重新部署新 revision。

4. 將部署輸出的 HTTPS 網址填入 LINE Webhook 設定，驗證 `/ping`、LINE Verify、指令查詢、AI 查詢及 `reset`。

AI 最多執行 5 次工具呼叫，45 秒內未完成會取消，單次最多 2500 output tokens。
目前是同步 webhook 架構，大批事件或慢速模型可能超過 LINE 回覆期限；這不是保證投遞的工作佇列。
同使用者的重疊 AI 請求會提示稍後再試；只快取最近一個成功事件，並非任意歷史事件的 exactly-once 處理。
建議設定 Google Cloud 與 OpenAI 預算告警，並依流量調整實例上限。

## API v2 遷移

依據 [最新 OpenAPI](https://api.nthusa.tw/openapi.json)（`info.version=2.0.0`）調整：

- 公車：`/buses/info/stops` 使用 `name`；`/buses/stops/{stop_name}` 使用扁平到站欄位，支援 null/空班次。
- 餐廳：`/dining/` 為建築與餐廳巢狀資料；週末查詢改用 `/dining/open?schedule=...`。
- 圖書館：`/libraries/space` 保留剩餘數量 0；`/libraries/rss/{rss_type}` 處理空頁及 nullable 連結。
- 地圖：使用 `/locations/search?query=...` 模糊搜尋。
- 公告：`/announcements/` 使用 department/language 篩選；新版 `title` 是文章標題，舊模組的佈告欄名稱改在本地篩選。移除 coroutine 快取，處理 nullable 文章欄位。

舊版程式曾包含硬編碼 Gemini 金鑰。移除檔案不會撤銷金鑰，也不會移除 Git 歷史；請在供應商 Console 撤銷或輪替。
OpenAI 設定 `store=False`，但仍會接收最近對話，且有供應商自身的資料政策；MCP 會接收模型產生的查詢參數。

## 測試

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

純單元測試不使用雲端金鑰或付費模型，Firestore 整合測試沒有 emulator 時會跳過。
安裝 Firebase CLI 與其支援的 Java（建議 21+）後可跑完整測試：

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