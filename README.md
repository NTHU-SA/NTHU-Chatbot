# NTHU-LINE-Bot

清華校園 LINE Bot「狗狗情報員」，使用 FastAPI、LINE Messaging API、LIFF、OpenAI Agents SDK、NTHU Data MCP 與 Firebase Firestore。
API 部署於 Cloud Run，LIFF 前端部署於 Firebase Hosting，不需要叢集或常駐背景 worker。

## 架構

```text
LINE 聊天室 ──webhook──▶ /callback ──▶ @ 指令模組（公車/餐廳/圖書館/地圖/公告…）→ NTHU REST API v2
                                   └─ 其他文字 → 回一則按鈕，開啟 LIFF 並帶入問題（按鈕綁定該對話，重新點擊回到同一串對話）

LIFF 網頁（Firebase Hosting：frontend/）
   │  CORS · Bearer id_token · X-Auth-Provider: line
   ▼
Cloud Run /api/* ──▶ AgentRunner（OpenAI Agents SDK）
                   ├─ MCP: https://api.nthusa.tw/mcp（9 個唯讀工具）
                   └─ Firestore: users / conversations / messages
                       ◀── SSE: user_message / tool_call_start / tool_call_end / token / done / error
```

- **聊天室只做「reply token 一定來得及」的事**；AI 對話在 LIFF 網頁進行（多 session、串流、即時顯示正在使用哪個工具）。
- 前後端分離：頁面由 Firebase Hosting 提供（CDN、頁面的 CSP 在 Hosting 設定），直接以 CORS 呼叫 Cloud Run。不用 Hosting rewrite 轉到 Cloud Run，因為 rewrite 有 60 秒上限，會切斷超過 60 秒的 SSE 回覆。API 只允許 `CORS_ALLOWED_ORIGINS` 列出的網域，不使用 cookie。
- 身分：LIFF `id_token` → 後端向 `https://api.line.me/oauth2/v2.1/verify` 驗證 → 對應到內部 user（見「Firebase 資料」）。Messaging API 與 LINE Login channel 必須在同一 Provider 下，userId 才相同。
- LLM 可接任何 OpenAI 相容端點（`OPENAI_BASE_URL`），預設 `gpt-5.6-luna`；MCP 工具由後端呼叫，白名單 `MCP_ALLOWED_TOOLS`。
- 群組內只使用 `@` 指令；其他文字會回帶問題的 LIFF 按鈕，不讀取私人對話。
- 輸入「說明」、「help」或 `@說明` 會回使用說明泡泡（指令清單依 `bot_config.yaml` 自動產生）。
- LIFF 對話：`REASONING_SUMMARY=true` 時會把模型的思考摘要串流顯示；模型缺少必要資訊時會反問並提供快速回覆按鈕（`suggest_replies` 工具）。
- 回呼等待處理完成才回應，沒有會在 Cloud Run 回應後被暫停的背景工作。
- 啟動時不建立 Rich Menu；更新選單請另外執行 `python -m scripts.rich_menu`（需要 `.env` 的 `LINE_CHANNEL_ACCESS_TOKEN` 與 `LIFF_ID`，第二頁最下方的區塊會直接開啟 LIFF 對話）。

Rich Menu 更新會取代現有選單，執行前請確認 `data/richmenu/menu-main.png`、`data/richmenu/menu-more.png` 均存在且正確。

## 目錄

```text
main.py / log.py                  啟動腳本、loguru 攔截
bot_config.yaml                   @ 指令模組與中文前綴對照
src/core/config.py                Settings（全部從環境變數讀取）
src/app/__init__.py               create_app()、lifespan 組裝 app.state
src/app/routes/callback.py        LINE webhook
src/app/routes/chat.py            /api/*（LIFF 對話，SSE）
src/app/auth/                     登入驗證（Authenticator）、外部身分 → 內部 user、限流
src/app/middleware.py             API 的安全標頭與 CORS
frontend/                         LIFF 前端（原生 JS ES modules，無建置工具；Firebase Hosting）
src/app/handlers/、src/modules/   聊天室內 @ 指令
src/application/                  資料模型；ChatStore / UserStore / ModuleRegistry 介面與記憶體實作
src/infrastructure/ai/            AgentRunner、狗狗情報員 prompt
src/infrastructure/firebase/      上述介面的 Firestore 實作
templates/messages/               Flex 訊息（含 LIFF 入口泡泡）
tests/                            pytest（fixture 在 conftest.py）
requirements*.in / *.txt          直接依賴 / uv 產生的含雜湊鎖定檔
pyproject.toml                    pytest、coverage、ruff 設定
.github/workflows/ci.yml          CI：鎖定檔檢查、ruff、pytest + Firestore emulator
cloudbuild.yaml                   Cloud Build：建置映像並更新 Cloud Run（兩個環境共用）
infra/                            環境建置與監測腳本、各環境設定（非機密）
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
2. **LINE Login channel**：Channel ID → `LINE_LOGIN_CHANNEL_ID`。新增 **LIFF app**：Endpoint URL `https://<HOSTING_SITE>.web.app/`（Firebase Hosting）、Size `Full`、
   Scopes 勾選 `profile` 與 **`openid`**（缺少就拿不到 id_token）。LIFF ID → `LIFF_ID`。
   在 LINE 以外的瀏覽器測試時，需把自己加入 Login channel 的 tester 或發佈 channel。

## 本機開發

需要 Python 3.12（與 Dockerfile、CI 一致）。以下為 PowerShell 指令：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-dev.txt
Copy-Item .env.template .env
```

在 `.env` 填入 LINE、LIFF、LLM 設定。不要覆蓋已有的 `.env`。
沒有 GCP 憑證時設 `CHAT_STORE=memory`（對話不會保存）；要用正式 Firestore 則：

```powershell
gcloud auth application-default login
.\.venv\Scripts\python.exe main.py
```

API 預設在 `http://localhost:5000`，健康檢查為 `/ping`。

前端是純靜態檔，可以用任何靜態伺服器開：

```powershell
Copy-Item frontend\config.example.json frontend\config.json   # 填入 LIFF ID 與 API 網址（git-ignored）
.\.venv\Scripts\python.exe -m http.server 5500 --directory frontend
```

並在 `.env` 設 `CORS_ALLOWED_ORIGINS=http://localhost:5500`。在 LINE 裡測試需要公開的 HTTPS 網址：用 `ngrok http 5000` 取得 API 網址填到 Webhook URL（`/callback`）與 `config.json` 的 `apiBase`；LIFF Endpoint URL 則要指向能公開存取的前端（例如另一個 ngrok 指向 5500，或直接部署到 staging Hosting）。

## Firebase 資料

Firestore Native mode `(default)`，由 `infra/bootstrap.sh` 建立；資料庫區域建立後不能更換。
結構依 `firebase.spec`：**高頻一起讀的放同一份文件、持續增長的用 subcollection、需要跨 user 查詢的放 top-level**。

### 身分：內部 user id 與外部登入方式分開

所有資料都以內部 `userId`（`usr_` + 128 bit 隨機值，建立後不可變）為主鍵。LINE 只是第一個連結到 user 的外部身分；
之後加學校 OAuth 或 Google，只需要新增一個 `Authenticator`（`src/app/auth/`），資料層不用改。

```text
請求 → Authenticator（依 X-Auth-Provider，目前只有 line）→ VerifiedIdentity
     → IdentityService：identityLookup/{sha256(provider:id)} → userId
     → 路由、限流、每日額度、所有資料都只認 userId
```

- `identityLookup` 的文件 ID 是雜湊值：學號這類個資不會出現在文件路徑、Console 與 log；原始外部 ID 只存在 `users/{uid}/identities/{provider}`。
- 第一次見到的身分會在同一個交易裡建立 user、identity 與 lookup；同一個 LINE 帳號同時進來也只會建立一個 user。
- 帳號連結（之後開放）**只能明確進行**：要在同一個請求裡同時證明兩個身分，絕不用 email、名稱或學號自動合併。同一個外部身分不能屬於兩個 user（衝突回 409 且不透露是誰），至少要保留一個登入方式，連結與解除都會寫進 `auditLog`。
- LINE Login channel 必須和 Messaging API channel 在同一個 Provider，否則 LIFF 的 `sub` 和 webhook 的 userId 不同，同一個人會變成兩個 user。
- 群組裡的發言者沒有和 bot 建立關係，webhook 不會為他們建立任何資料。

### 路徑

| 路徑 | 資料 |
| --- | --- |
| `identityLookup/{sha256(provider:id)}` | `userId`、`provider`、`createdAt` |
| `users/{uid}` | `status`（active / blocked / deleted）、`displayName`、`pictureUrl`、`createdAt`、`updatedAt`、`lastActiveAt`、`lastConversationId`、`lastModuleId`、`lastModuleUsedAt`、`conversationCount` |
| `users/{uid}/identities/{provider}` | `providerUserId`、`linkedAt`、`lastLoginAt`、`metadata`（provider 專屬：LINE 的 `followed`、`liff`{os、appVersion、language、contextType、friendshipStatus}；前端自報，只當參考，不參與授權） |
| `users/{uid}/auditLog/{id}` | `action`（link / unlink …）、`provider`、`at`、`expiresAt`（TTL 365 天） |
| `users/{uid}/moduleStates/{moduleId}` | `@` 指令模組的 `lastUsedAt`、`usageCount`；`onboarding` 文件記錄首次使用是否已問過稱呼與系所（asked / done / skipped） |
| `users/{uid}/preferences/{nickname\|department}` | `value`、`source`（user：設定頁；assistant：對話中由 AI 記下）、`updatedAt` |
| `users/{uid}/memory/{id}` | 使用者要求記住的事：`value`（≤100 字）、`sourceConversationId`、`createdAt`；每人最多 20 則 |
| `users/{uid}/usage/{YYYY-MM-DD}` | 每日 LLM 訊息計數 `count`、`expiresAt`（TTL 8 天） |
| `users/{uid}/consents/{type}_v{version}` | 隱私權政策同意紀錄：`status`（accepted / revoked）、`acceptedAt`、`revokedAt`、`source`；每個版本一份，不覆蓋 |
| `users/{uid}/conversationOrigins/{sha256(origin)}` | LINE 泡泡 → `conversationId`（同一顆按鈕永遠開同一個對話，刪除後重建） |
| `users/{uid}/conversationCleanup/{cid}` | 已刪除、訊息尚未清完的對話（中斷後下次繼續清） |
| `conversations/{cid}` | `userId`、`channel`、`status`、`title`、`startedAt`、`lastMessageAt`、`messageCount`、`metadata.origin`；每人最多 50 個，超過時刪除最久未更新的 |
| `conversations/{cid}/messages/{mid}` | `role`、`contentType`、`content`、`toolCalls`、`model`、`promptVersion`、`tokenUsage`、`latencyMs`、`createdAt` |
| `modules/{moduleId}` | `enabled: false` 可暫停某個 `@` 指令模組（沒有文件代表啟用；快取 60 秒） |

每一則訊息是一份文件，不存成 array；對話與訊息的讀取都會比對 `userId`，別人的對話一律當作不存在。
`tokenUsage`、`model`、`promptVersion` 只存在資料庫供成本與品質分析，不會回傳給前端。

## 工具呼叫與搜尋

- 模型可在同一步一次呼叫多個彼此獨立的工具（`parallel_tool_calls`），SDK 會並行執行。
- 唯讀、與使用者無關的 MCP 結果在每個實例內短暫快取（公告 5 分鐘、課程與地點 1 小時；公車即時資料不快取），快取命中也計入次數。
- 每則訊息最多 `MAX_TOOL_CALLS_PER_MESSAGE`（預設 6）次外部呼叫，超過時工具回報上限、模型用已查到的資料回答；每次執行的次數會記在 log（`Agent run finished`）。
- **網路搜尋**（`WEB_SEARCH_ENABLED=true`，預設關閉；需要官方 OpenAI + Responses API）：包成我們自己的 `web_search` function tool，內部以 Responses API 的 web_search 搜尋，`allowed_domains` 只允許 `WEB_SEARCH_DOMAINS`（預設 nthu.edu.tw，含子網域），回傳的來源網址會在伺服器端再過濾一次（只留 https 且網域相符）。不直接掛 hosted web search，是為了在搜尋結果進入對話前就能計次並標記「本輪已讀取外部資料」（個人化寫入工具因此停用）。每則訊息最多 `MAX_WEB_SEARCHES_PER_MESSAGE` 次；每次搜尋另外計費。

## 個人化（稱呼、系所、記住的事）

- 首次同意隱私權政策後，會詢問稱呼與系所（可略過）；沒填也沒略過的話，AI 會在第一次回答的最後問一次，之後不再問。
- 對話中由 AI 判斷要不要記：`save_profile`（稱呼、系所）、`remember` / `forget`（使用者明確要求記住的事）。系所會比對 NTHU API `/departments/` 的官方學術單位名稱（加上常見簡稱表），有多個可能時請使用者選。
- **防 prompt injection**：同一輪只要讀過外部資料（任何 MCP 工具結果或網路搜尋），寫入工具一律拒絕，只有使用者自己說的話能被記下。寫入的文字會去掉換行與角括號、限制長度，注入 instructions 時包在 `<user_profile>` 區塊並標明「不是指令」。
- 記下的內容不會出現在訊息的 `toolCalls` 裡；使用者可在側欄「我的資料」修改或刪除，刪除帳號時一併刪除。

## 隱私權與個人資料

- 隱私權政策在 `frontend/privacy.html`（**草稿，需學生會審閱定稿並填入聯絡方式**），依個資法第 8 條列出蒐集者、目的、資料類別、期間/地區/對象（含 OpenAI 美國）、當事人權利與不提供的影響。
- 第一次開啟 LIFF 頁面會顯示同意畫面；**後端強制**：未同意目前版本時，送出訊息回 `403 {"code": "consent_required"}`，內容不會送到 LLM。`@` 指令不經過 AI，不需要同意。
- 政策改版時遞增 `PRIVACY_POLICY_VERSION`（預設 `1`），所有人下次使用 AI 對話前要重新同意；舊版本的同意紀錄保留。
- 使用者可在側欄**撤回同意**（之後無法使用 AI 對話，資料保留）或**刪除我的所有資料**（`DELETE /api/me`：對話、訊息、同意紀錄、使用紀錄、外部身分對應與帳號本身全部刪除；之後同一個 LINE 帳號會是全新的使用者）。
- 政策寫明「維運存取會被記錄」：prod 上線前要開啟 Firestore 的 Data Access 稽核 log。

Firestore 規則拒絕所有用戶端直接存取；後端透過服務帳號 IAM 存取，不依賴 Firebase Auth。
規則、複合索引、大型欄位的索引豁免與 TTL 都由 `infra/bootstrap.sh` 依 `firestore.rules`、`firestore.indexes.json` 套用。

## Cloud Run 部署

staging（`dev` 分支）與 prod（`main` 分支）放在兩個獨立的 GCP 專案，建置與設定都寫成可重複執行的腳本。
完整步驟、權限設計與 Secret 加入方式見 **[infra/README.md](infra/README.md)**。

```bash
bash infra/bootstrap.sh infra/environments/prod.conf                            # Firestore、AR、SA、Secret、Cloud Run、trigger
ALERT_EMAIL=you@example.com bash infra/monitoring.sh infra/environments/prod.conf  # uptime check、告警
```

- 部署：push 到對應分支 → Cloud Build（`cloudbuild.yaml`，使用專用的最小權限部署 SA）建置映像 → 只更新 Cloud Run 的映像。環境變數與 Secret 設定在服務上，每個新 revision 自動沿用；GitHub 端不需要任何 GCP 金鑰。
- `--timeout=180` 必須大於 LIFF 對話的 120 秒 agent 上限（SSE 長連線）；使用非官方端點時再加 `OPENAI_BASE_URL`。
- 執行期 SA 透過 ADC 存取 Firestore，不使用 JSON 私鑰。不得在 Cloud Run 設定 `FIRESTORE_EMULATOR_HOST`。更新 Secret 後要部署新 revision 才會生效。
- Cloud Run 上的 log 是帶 `severity` 的 JSON（見 `log.py`），可以在 Logs Explorer 用 `severity>=ERROR` 篩選。
- 前端：push 到 `dev` / `main` 且測試通過後，CI 以 Workload Identity Federation 部署到 Firebase Hosting（`infra/build_frontend.py` 依環境產生 `config.json` 與 CSP）；手動部署用 `bash infra/deploy_frontend.sh infra/environments/<env>.conf`。
- 部署後把 API 網址填進 LINE Webhook（`/callback`）、Hosting 網址填進 LIFF Endpoint URL，再驗證 `/ping`、LINE Verify、指令查詢，以及聊天室提問 → 開啟 LIFF → 串流回覆。

LIFF 對話單次最多 `MAX_AGENT_TURNS` 回合、120 秒逾時、每人每日 `DAILY_MESSAGE_LIMIT` 則；單一實例另有突發限流。
`RateLimiter`、id_token 快取與 MCP 連線都是單一實例狀態；每日額度存在 Firestore，跨實例仍正確。
建議設定 Google Cloud 與 LLM 供應商的預算告警，並依流量調整實例上限（`MAX_INSTANCES`）。

## API v2 遷移

依據 [最新 OpenAPI](https://api.nthusa.tw/openapi.json)（`info.version=2.0.0`）調整：

- 公車：`/buses/info/stops` 使用 `name`；`/buses/stops/{stop_name}` 使用扁平到站欄位，支援 null/空班次。
- 餐廳：`/dining/` 為建築與餐廳巢狀資料；週末查詢改用 `/dining/open?schedule=...`。
- 圖書館：`/libraries/space` 保留剩餘數量 0；`/libraries/rss/{rss_type}` 處理空頁及 nullable 連結。
- 地圖：使用 `/locations/search?query=...` 模糊搜尋。
- 公告：`/announcements/` 使用 department/language 篩選；新版 `title` 是文章標題，舊模組的佈告欄名稱改在本地篩選。移除 coroutine 快取，處理 nullable 文章欄位。

舊版程式曾包含硬編碼 Gemini 金鑰。移除檔案不會撤銷金鑰，也不會移除 Git 歷史；請在供應商 Console 撤銷或輪替。
LLM 供應商會接收最近 `HISTORY_WINDOW` 則對話，且有其自身的資料政策；MCP 會接收模型產生的查詢參數。Agents SDK 的 tracing 已停用，不會把對話送到 OpenAI 追蹤後端。

## 依賴管理

直接依賴寫在 `requirements.in`（執行期）與 `requirements-dev.in`（測試/CI 工具）；
`requirements*.txt` 是由 [uv](https://docs.astral.sh/uv/) 產生、含雜湊的跨平台鎖定檔，**不要手動編輯**。
Dockerfile 與 CI 都以 `--require-hashes` 安裝：任何套件（含間接依賴）的內容和鎖定的雜湊不符，安裝就會失敗。

```powershell
.\.venv\Scripts\python.exe -m pip install uv   # 只在本機用來產生鎖定檔
# 修改 .in 後重新產生鎖定檔（其他套件會保留既有版本）
.\.venv\Scripts\uv pip compile requirements.in --universal --generate-hashes --python-version 3.12 -o requirements.txt
.\.venv\Scripts\uv pip compile requirements-dev.in --universal --generate-hashes --python-version 3.12 -o requirements-dev.txt
# 要升級某個套件時加上 --upgrade-package <name>
```

CI 會重新編譯並比對，鎖定檔與 `.in` 不一致時 PR 會失敗。Dependabot 每週更新 pip 套件、Docker base image 與 GitHub Actions（小版本合併成一個 PR）。

## 測試

測試使用 pytest（`asyncio_mode = "auto"`；共用 fixture 在 `tests/conftest.py`，假物件在 `tests/fakes.py`），設定寫在 `pyproject.toml`。

```powershell
.\.venv\Scripts\python.exe -m pytest -q --cov   # 含覆蓋率，低於門檻會失敗
.\.venv\Scripts\python.exe -m ruff check .
```

純單元測試不使用雲端金鑰或付費模型（LLM、LINE 驗證與 Firestore 皆以假物件或記憶體實作取代），Firestore 整合測試（`@pytest.mark.firestore`）沒有 emulator 時會跳過。
安裝 Firebase CLI 與 Java 21+ 後可跑完整測試：

```powershell
firebase emulators:exec --only firestore --project demo-nthu-chatbot ".venv\Scripts\python.exe -m pytest -q"
```

本機使用 emulator 開發時，在另一個終端執行 `firebase emulators:start --only firestore --project demo-nthu-chatbot`，
並將應用的 `GOOGLE_CLOUD_PROJECT=demo-nthu-chatbot`、`FIRESTORE_EMULATOR_HOST=127.0.0.1:8085` 設為一致。

### CI

`.github/workflows/ci.yml` 會在每個 PR 與每次 push 到 `main` / `dev` 時執行：檢查鎖定檔是否最新 → `ruff check` → 在 Firestore emulator 下執行 `pytest --cov`（含整合測試）。
workflow 只有 `contents: read` 權限，第三方 action 都釘選 commit SHA。建議在 `main`、`dev` 開啟 branch protection，要求經過 PR 且 CI 通過才能合併；這樣 Cloud Build 只會部署通過測試的程式。

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