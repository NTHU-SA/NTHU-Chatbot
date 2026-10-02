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
src/app/security.py               id_token 驗證、限流
src/app/middleware.py             安全標頭與 CSP
src/app/static/liff/              LIFF 前端（原生 JS）
src/app/handlers/、src/modules/   聊天室內 @ 指令
src/application/                  資料模型、ChatStore 介面與記憶體實作
src/infrastructure/ai/            AgentRunner、狗狗情報員 prompt
src/infrastructure/firebase/      FirestoreChatStore
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
2. **LINE Login channel**：Channel ID → `LINE_LOGIN_CHANNEL_ID`。新增 **LIFF app**：Endpoint URL `https://YOUR_SERVICE_URL/liff/`、Size `Full`、
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
- 部署後把服務網址填進 LINE Webhook（`/callback`），再驗證 `/ping`、`/api/config`、LINE Verify、指令查詢，以及聊天室提問 → 開啟 LIFF → 串流回覆。

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