# NTHU-LINE-Bot

清華大學學生會的校園 LINE Bot「清華校園情報員」。聊天室裡用 `@` 指令快速查公車、餐廳、圖書館、地圖與公告；
自由提問則開啟 LIFF 網頁，由 AI 查校園資料後串流回答。

- **後端**：FastAPI on Cloud Run（LINE webhook、LIFF 對話 API、SSE 串流）
- **前端**：原生 JS（ES modules、無建置工具）on Firebase Hosting
- **AI**：OpenAI Agents SDK + NTHU Data MCP（9 個唯讀校園工具），可選擇開啟只限 nthu.edu.tw 的網路搜尋
- **資料**：Firestore（Native mode），後端以服務帳號存取，瀏覽器不直接連

## 目錄

- [架構](#架構)
- [本機開發](#本機開發)
- [設定](#設定)
- [LINE Developers 設定](#line-developers-設定)
- [身分與資料](#身分與資料)
- [AI 對話](#ai-對話)
- [隱私權與資安](#隱私權與資安)
- [部署與環境](#部署與環境)
- [開發流程](#開發流程)
- [附錄](#附錄)

## 架構

```text
LINE 聊天室 ──webhook──▶ Cloud Run /callback
                          ├─ @ 指令 → 指令模組（公車 / 餐廳 / 圖書館 / 地圖 / 公告…）→ NTHU REST API v2
                          └─ 其他文字 → 回一顆按鈕，開啟 LIFF 並帶入問題（同一顆按鈕永遠回到同一串對話）

LIFF 網頁（Firebase Hosting：frontend/）
   │  CORS · Authorization: Bearer <LIFF id_token> · X-Auth-Provider: line
   ▼
Cloud Run /api/* ──▶ AgentRunner（OpenAI Agents SDK）
   │                  ├─ MCP: https://api.nthusa.tw/mcp（唯讀校園工具，結果短暫快取）
   │                  ├─ 個人化工具：save_profile / remember / forget
   │                  └─ web_search（選用，只搜 nthu.edu.tw）
   ├─ Firestore：users / identityLookup / conversations / messages …
   └─ SSE ──▶ thinking · interim · tool_call_start / end · suggestions · memory · token · done · error
```

- **聊天室只做「reply token 一定來得及」的事**；AI 對話在 LIFF 網頁進行（多個對話、串流、即時顯示正在查什麼）。群組裡只用 `@` 指令，不讀取私人對話。
- **前後端分離**：頁面由 Hosting 的 CDN 提供，頁面的 CSP 設在 Hosting；前端直接以 CORS 呼叫 Cloud Run。不使用 Hosting rewrite 轉到 Cloud Run，因為 rewrite 有 60 秒上限，會切斷較長的 SSE 回覆。
- **身分**：LIFF `id_token` 由後端向 LINE 驗證後，對應到內部 user（見「身分與資料」）；所有資料只認內部 user id。
- 輸入「說明」、「help」或 `@說明` 會回使用說明泡泡（指令清單依 `bot_config.yaml` 自動產生）。
- webhook 等事件處理完才回應；非關鍵寫入（最近活動時間、模組使用次數）與請求並行，回應前收尾，不留會被 Cloud Run 暫停的背景工作。

### 目錄結構

```text
main.py / log.py                  啟動腳本；loguru（Cloud Run 上輸出帶 severity 的 JSON）
bot_config.yaml                   @ 指令模組與中文前綴對照
src/core/config.py                Settings（全部從環境變數讀取，格式不符時啟動失敗）
src/app/__init__.py               create_app()、lifespan 組裝 app.state
src/app/routes/callback.py        LINE webhook
src/app/routes/chat.py            /api/sessions…（LIFF 對話，SSE）
src/app/routes/account.py         /api/me、同意紀錄、個人資料、刪除我的資料
src/app/auth/                     Authenticator（目前為 LINE）、外部身分 → 內部 user、限流
src/app/middleware.py             API 的安全標頭與 CORS
src/app/handlers/、src/modules/   聊天室內 @ 指令
src/application/                  資料模型；ChatStore / UserStore / ModuleRegistry 介面與記憶體實作；系所名稱正規化
src/infrastructure/ai/            AgentRunner、prompt、個人化工具、網路搜尋、單次執行狀態
src/infrastructure/firebase/      上述介面的 Firestore 實作
templates/messages/               Flex 訊息（含 LIFF 入口泡泡）
frontend/                         LIFF 前端與隱私權政策頁
infra/                            環境建置、監測、前端部署腳本與各環境設定（非機密）
cloudbuild.yaml                   Cloud Build：建置映像並更新 Cloud Run（兩個環境共用）
tests/                            pytest（fixture 在 conftest.py，假物件在 fakes.py）
pyproject.toml / uv.lock          依賴（執行期、dev、test group）與工具設定 / uv 產生的鎖定檔
.pre-commit-config.yaml           isort、Black、Ruff 與 uv.lock 檢查（本機與 CI 共用）
```

程式沿用 `src/{app,application,core,infrastructure}` 分層，而不是資訊處開發守則給新專案的 `src/<package>/`：這個 repo 早於守則，搬移會改動所有 import，目前沒有實際好處。

前端 CSS 的顏色、字型、字級、間距與圓角一律使用 `frontend/style.css` `:root` 的變數（深色模式透過 `data-theme` 覆寫顏色變數），不要在規則裡寫死數值。

對話介面採 Soft Glass 風格，主色沿用 richmenu 常用功能頁籤的灰紫色 `#7362a2`，搭配淡紫白背景與有限的玻璃層次（工具列、側欄、輸入框）。不支援 `backdrop-filter` 時使用實心表面；深色模式另設對比色票。新訊息才播放入場動畫，載入歷史訊息不重播；所有動畫回應 `prefers-reduced-motion`。圖示使用頁內 SVG，不需額外下載字型或圖示套件。

輸入框上方固定顯示 AI 回覆可能出錯的提醒，並以 `aria-describedby` 關聯到輸入欄位。

側欄「外觀」可選擇跟隨系統、淺色或深色，預設跟隨系統。選擇僅保存在此瀏覽器的 `localStorage`（`nthu-chatbot-theme`），不送到後端；對話頁與隱私頁共用設定。`frontend/js/theme.js` 在 CSS 載入前套用外觀，避免手動深色設定閃成淺色；儲存被瀏覽器阻擋時仍可切換，並顯示無法保存的提醒。

外觀設定的無相依測試：`node --test tests\frontend_theme.test.cjs`。

## 本機開發

需要 [uv](https://docs.astral.sh/uv/)；Python 版本寫在 `.python-version`（3.14，與 `pyproject.toml`、Dockerfile、CI 一致），uv 會自動準備。以下為 PowerShell 指令：

```powershell
uv sync --locked                  # 依 uv.lock 建立 .venv（含 dev 與 test group）
uv run pre-commit install         # 之後每次 commit 自動跑 isort、Black、Ruff
Copy-Item .env.template .env      # 填入 LINE、LIFF、LLM 設定；不要覆蓋已有的 .env
```

沒有 GCP 憑證時設 `CHAT_STORE=memory`（資料不會保存）；要連 Firestore：

```powershell
gcloud auth application-default login
uv run main.py                    # API 在 http://localhost:5000，健康檢查 /ping
```

前端是純靜態檔：

```powershell
Copy-Item frontend\config.example.json frontend\config.json   # 填入 LIFF ID 與 API 網址（git-ignored）
uv run python -m http.server 5500 --directory frontend
```

並在 `.env` 設 `CORS_ALLOWED_ORIGINS=http://localhost:5500`。在 LINE 裡測試需要公開的 HTTPS 網址：
用 `ngrok http 5000` 取得 API 網址，填到 Webhook URL（`/callback`）與 `config.json` 的 `apiBase`；LIFF Endpoint URL 要指向能公開存取的前端（另一個 ngrok 指向 5500，或直接部署到 staging）。

## 設定

**所有憑證只從環境變數讀取，任何檔案都不得含真實值。** 本機使用 git-ignored 的 `.env`（由 `.env.template` 複製），雲端由 Secret Manager 注入。
`.gitignore`、`.dockerignore`、`.gcloudignore` 均排除 `.env*` 與金鑰檔。Firestore 不用金鑰檔：Cloud Run 用執行期服務帳號的 ADC，本機用 `gcloud auth application-default login`。

| 類別 | 變數 |
| --- | --- |
| 機密（Secret Manager） | `LINE_CHANNEL_SECRET`、`LINE_CHANNEL_ACCESS_TOKEN`、`OPENAI_API_KEY` |
| LINE Login / LIFF | `LINE_LOGIN_CHANNEL_ID`、`LIFF_ID`（部署環境不寫在 repo：Cloud Run 環境變數與 repo variable `LIFF_ID_<ENV>`，見 `infra/README.md`） |
| 前端 | `CORS_ALLOWED_ORIGINS`（逗號分隔；只接受 https 網域，本機可用 `http://localhost:<port>`） |
| LLM | `OPENAI_MODEL`、`OPENAI_BASE_URL`（選填，任何 OpenAI 相容端點）、`OPENAI_USE_RESPONSES_API`、`REASONING_SUMMARY`、`MAX_OUTPUT_TOKENS`、`MAX_OUTPUT_CHARS` |
| MCP | `MCP_SERVER_URL`、`MCP_ALLOWED_TOOLS`（逗號分隔）、`MCP_TIMEOUT_SECONDS` |
| 工具與搜尋 | `MAX_TOOL_CALLS_PER_MESSAGE`、`WEB_SEARCH_ENABLED`、`WEB_SEARCH_MODEL`、`WEB_SEARCH_DOMAINS`、`MAX_WEB_SEARCHES_PER_MESSAGE` |
| 隱私權 | 政策版本寫在 `src/core/privacy.py`（不是環境變數；改版時連同 `frontend/privacy.html` 一起改） |
| 儲存 | `CHAT_STORE`（`firestore` / `memory`）、`GOOGLE_CLOUD_PROJECT` |
| 限制 | `HISTORY_WINDOW`、`HISTORY_MESSAGE_CHARS`、`MAX_TOOL_OUTPUT_CHARS`、`MAX_MESSAGE_CHARS`、`DAILY_MESSAGE_LIMIT`、`MAX_AGENT_TURNS`、`TOOL_RESULT_PREVIEW_CHARS` |

每個變數的說明與預設值見 `.env.template`。

## LINE Developers 設定

同一個 **Provider** 底下建立兩個 channel（不同 Provider 的 userId 不同，同一個人會變成兩個 user）：

1. **Messaging API channel**：Channel secret → `LINE_CHANNEL_SECRET`；長期 Channel access token → `LINE_CHANNEL_ACCESS_TOKEN`。
   Webhook URL 設為 `https://<API 網址>/callback`，啟用 Webhook，停用衝突的自動回應與歡迎訊息。
2. **LINE Login channel**：Channel ID → `LINE_LOGIN_CHANNEL_ID`。新增 **LIFF app**：Endpoint URL `https://<HOSTING_SITE>.web.app/`、Size `Full`、
   Scopes 勾選 `profile` 與 **`openid`**（缺少就拿不到 id_token）。LIFF ID → `LIFF_ID`。
   在 LINE 以外的瀏覽器測試時，要把自己加入 Login channel 的 tester 或發佈 channel。

Rich Menu 不在啟動時建立；更新選單請執行 `uv run python -m scripts.rich_menu`，需要該環境的 `LINE_CHANNEL_ACCESS_TOKEN` 與 `LIFF_ID`（環境變數或 `.env`）。
這會取代現有選單，執行前確認 `data/richmenu/menu-main.png`、`data/richmenu/menu-more.png` 都正確。

## 身分與資料

Firestore Native mode `(default)`，由 `infra/bootstrap.sh` 建立；區域建立後不能更換。
結構依 `firebase.spec`：**高頻一起讀的放同一份文件、持續增長的用 subcollection、需要跨 user 查詢的放 top-level、歷史資料保留版本**。

### 內部 user id 與外部登入方式分開

所有資料都以內部 `userId`（`usr_` + 128 bit 隨機值，建立後不可變）為主鍵。LINE 只是第一個連結到 user 的外部身分；
之後加學校 OAuth 或 Google，只要新增一個 `Authenticator`（`src/app/auth/`）並在 `create_app()` 註冊，資料層不用改。

```text
請求 → Authenticator（依 X-Auth-Provider，目前只有 line；未知的一律 401）→ VerifiedIdentity
     → IdentityService：identityLookup/{sha256(provider:id)} → userId
     → 路由、限流、每日額度、所有資料都只認 userId
```

- `identityLookup` 的文件 ID 是雜湊值：學號這類個資不會出現在文件路徑、Console 與 log；原始外部 ID 只存在 `users/{uid}/identities/{provider}`。API 不回傳任何 ID。
- 第一次見到的身分以一次原子寫入建立 user、identity 與 lookup（`create()`，已存在就失敗）；同一個帳號同時進來也只會建立一個 user。
- 帳號連結（之後開放）**只能明確進行**：同一個請求裡同時證明兩個身分，絕不用 email、名稱或學號自動合併；同一個外部身分不能屬於兩個 user（衝突回 409 且不透露是誰）；至少保留一個登入方式；連結與解除都寫入 `auditLog`。
- 群組裡的發言者沒有和 bot 建立關係，webhook 不會為他們建立任何資料。

### 路徑

| 路徑 | 資料 |
| --- | --- |
| `identityLookup/{sha256(provider:id)}` | `userId`、`provider`、`createdAt` |
| `quotaCarryover/{sha256(provider:id)}` | 刪除帳號時保留當日 AI 用量：`day`、`count`、`expiresAt`（TTL 2 天）。同一個外部身分當天重建帳號時沿用，避免以刪除帳號重置每日上限 |
| `users/{uid}` | `status`（active / blocked / deleting / deleted；deleted 只剩不含個資的墓碑與 `expiresAt` TTL）、`displayName`、`pictureUrl`、`createdAt`、`updatedAt`、`lastActiveAt`、`lastConversationId`、`lastModuleId`、`lastModuleUsedAt`、`conversationCount` |
| `users/{uid}/identities/{provider}` | `providerUserId`、`linkedAt`、`lastLoginAt`、`metadata`（LINE：`followed`、`liff`{os、appVersion、language、contextType、friendshipStatus}；前端自報，不參與授權；不收 contextId） |
| `users/{uid}/consents/{type}_v{version}` | 同意紀錄：`status`（accepted / revoked）、`acceptedAt`、`revokedAt`、`source`；每個版本一份，不覆蓋 |
| `users/{uid}/preferences/{nickname\|department}` | `value`、`source`（user：設定頁；assistant：對話中由 AI 記下）、`updatedAt` |
| `users/{uid}/memory/{m00…m19}` | 使用者要求記住的事：`value`（≤100 字）、`sourceConversationId`、`createdAt`；每則占一個固定格子，以 `create()` 搶空格，並行也不會超過 20 則 |
| `users/{uid}/moduleStates/{moduleId}` | `@` 指令的 `lastUsedAt`、`usageCount`；`onboarding` 記錄首次使用是否已問過稱呼與系所 |
| `users/{uid}/usage/{YYYY-MM-DD}` | 每日 LLM 訊息計數 `count`、`expiresAt`（TTL 8 天） |
| `users/{uid}/auditLog/{id}` | 連結、解除、同意、撤回：`action`、`at`、`expiresAt`（TTL 365 天） |
| `users/{uid}/conversationOrigins/{sha256(origin)}` | LINE 泡泡 → `conversationId` |
| `users/{uid}/conversationCleanup/{cid}` | 已刪除、訊息尚未清完的對話（中斷後下次繼續清） |
| `conversations/{cid}` | `userId`、`channel`、`status`、`title`、`startedAt`、`lastMessageAt`、`messageCount`、`metadata.origin`；每人最多 50 個，超過時刪除最久未更新的 |
| `conversations/{cid}/messages/{mid}` | `role`、`contentType`、`content`、`toolCalls`、`model`、`promptVersion`、`tokenUsage`、`latencyMs`、`createdAt` |
| `modules/{moduleId}` | `enabled: false` 暫停某個 `@` 指令模組（沒有文件代表啟用；快取 60 秒；沒有後台時在 Console 開關） |

- 每則訊息一份文件，不存成 array；讀取對話與訊息一律比對 `userId`，別人的對話當作不存在。
- `tokenUsage`、`model`、`promptVersion`、`latencyMs` 只存在資料庫供成本與品質分析，不回傳前端。
- 建立對話與首次登入不用「先讀再寫」的交易（多個並行請求會互卡讀鎖升級）：改用 create-only 的 batch 與帶前置條件的刪除。
- 規則（全部拒絕）、複合索引、大型欄位的索引豁免與 TTL 都由 `infra/bootstrap.sh` 依 `firestore.rules`、`firestore.indexes.json` 套用。

## AI 對話

- **串流**：思考摘要（`REASONING_SUMMARY=true`）、呼叫工具前的過場句（移到「過程」卡片）、工具卡片、反問時的快速回覆按鈕（`suggest_replies`），最後才是正式回答。
- **限制**：每則訊息最多 `MAX_AGENT_TURNS` 回合、120 秒逾時、`MAX_TOOL_CALLS_PER_MESSAGE` 次外部呼叫；每人每日 `DAILY_MESSAGE_LIMIT` 則（跨實例，存在 Firestore），單一實例另有突發限流。每次執行的工具次數記在 log（`Agent run finished`）。
- **工具效率**：彼此獨立的查詢在同一步一起發出、並行執行（`parallel_tool_calls`）；唯讀、與使用者無關的 MCP 結果在每個實例內短暫快取（公告 5 分鐘、課程與地點 1 小時；公車即時資料不快取），快取命中也計次。
- **網路搜尋**（`WEB_SEARCH_ENABLED=true`，預設關閉；需要官方 OpenAI + Responses API；每次搜尋另外計費）：包成我們自己的 `web_search` function tool，內部以 Responses API 的 web_search 搜尋，`allowed_domains` 只允許 `WEB_SEARCH_DOMAINS`（預設 nthu.edu.tw，含子網域），回傳的來源網址在伺服器端再過濾一次。不直接掛 hosted web search，是為了在搜尋結果進入對話前就能計次並標記「本輪已讀取外部資料」。
- **個人化**：首次同意後會詢問稱呼與系所（可略過）；沒填也沒略過的話，AI 在第一次回答最後問一次。對話中使用者明確說出時，AI 用 `save_profile` / `remember` / `forget` 記下或刪除；系所比對 NTHU API `/departments/` 的官方學術單位名稱（只取名稱，不存人員資料）與常見簡稱表，有多個可能時讓使用者選。使用者可在側欄「我的資料」修改或刪除。
- **Prompt 版本**：修改 `src/infrastructure/ai/prompts.py` 的 system prompt 時遞增 `PROMPT_VERSION`；每則回答都會記下版本。

## 隱私權與資安

### 隱私權

- 隱私權政策在 `frontend/privacy.html`（**草稿，需學生會審閱定稿並填入聯絡方式**），依個資法第 8 條列出蒐集者、目的、資料類別、期間 / 地區 / 對象（含 OpenAI 美國）、當事人權利與不提供的影響。
- **後端強制同意**：未同意目前版本時，送出訊息回 `403 {"code": "consent_required", "version": …}`，內容不會送到 LLM；`@` 指令不經過 AI，不需要同意。
- **政策版本只有一個來源**：`src/core/privacy.py`。後端以它決定要同意哪一版；`infra/build_frontend.py` 把同一個值寫進 `privacy.html` 的版本與 `config.json`。前端送出同意時帶的是畫面上顯示的版本，前後端尚未同步部署時後端回 409，前端請使用者稍後再開，不會記錄成沒看過的版本。改版時修改政策內容並遞增版本，舊版本的紀錄保留。
- 使用者可在側欄**撤回同意**（之後無法使用 AI 對話，資料保留）或**刪除我的所有資料**（`DELETE /api/me`：對話、訊息、個人化資料、同意與使用紀錄、外部身分對應與帳號本身全部刪除；之後同一個 LINE 帳號是全新的使用者）。
- **刪除流程**：先把帳號標成 `deleting`（其他請求一律 403，只能再呼叫刪除）→ 刪除對話與 user 資料，每一步重新查詢確認清空 → user 文件換成不含個資的墓碑（`status=deleted`，`expiresAt` TTL 1 天）→ 再清一次。墓碑之後才完成的寫入會讀到 `deleted` 並撤銷自己，所以刪除與進行中的請求同時發生也不會留下資料。沒刪乾淨時回 `503 deletion_incomplete`，帳號維持 `deleting`，使用者重新開啟頁面會看到「完成刪除」的按鈕。
- LLM 供應商會收到最近 `HISTORY_WINDOW` 則對話與使用者資料區塊；MCP 只收到模型產生的查詢參數。Agents SDK 的 tracing 已停用。

### 資安重點

- **登入**：只採用 provider 驗證過的身分（LINE verify endpoint 檢查 `aud`、`iss`）；顯示名稱與頭像只來自驗證過的 claims；未知的 provider 與無效 token 回同樣的 401。
- **API**：CORS 只允許設定的前端網域且不帶 cookie；API 回應帶 `default-src 'none'`、`X-Frame-Options: DENY`；`docs_url` 等文件端點關閉。
- **前端**：Hosting 設定嚴格 CSP（`connect-src` 只允許該環境的 API 與 LINE）；模型輸出經 DOMPurify 消毒，連結只允許 http(s) / mailto。
- **防 prompt injection**：同一輪只要讀過外部資料（任何 MCP 工具結果或網路搜尋），個人化寫入工具一律拒絕；寫入的文字去掉換行與角括號、限制長度，注入 instructions 時包在 `<user_profile>` 並標明「不是指令」；工具結果在 prompt 中也被標示為資料。
- **資料**：Firestore rules 全部拒絕，只有後端服務帳號能存取；執行期與部署服務帳號都是最小權限（見 `infra/README.md`）；log 只記例外型別名稱，不記內容、token 或任何 ID。
- **供應鏈**：依賴以雜湊鎖定（`--require-hashes`）、Docker base image 釘 digest、GitHub Actions 釘 commit SHA；CI 以 Workload Identity Federation 部署，不存任何 GCP 金鑰。

## 部署與環境

staging 與 prod 在同一個 GCP 專案，Firestore 資料庫、Secret、service account、WIF pool、Cloud Run 服務與 Hosting 網站都分開，權限只授予自己那一份。建置、監測與前端部署都是可重複執行的腳本，詳見 **[infra/README.md](infra/README.md)**。

| | staging | prod |
| --- | --- | --- |
| 分支 | `dev` | `main` |
| GCP 專案 | `nthusa-chatbot` | `nthusa-chatbot`（Firestore 資料庫 `prod`、Secret 加 `-prod`） |
| API | Cloud Run `nthu-chatbot-staging` | Cloud Run `nthu-chatbot` |
| 前端 | `nthusa-chatbot.web.app` | `nthusa-chatbot-prod.web.app` |

```bash
LINE_LOGIN_CHANNEL_ID=... LIFF_ID=... bash infra/bootstrap.sh infra/environments/prod.conf  # Firebase、Firestore、AR、SA、WIF、Secret、Cloud Run、trigger
ALERT_EMAIL=you@example.com bash infra/monitoring.sh infra/environments/prod.conf  # 5xx 與 ERROR log 告警（防冷啟動用外部 ping 服務）
```

- **API**：push 到對應分支 → Cloud Build（`cloudbuild.yaml`，專用的最小權限部署 SA）建置映像 → 只更新 Cloud Run 的映像。環境變數與 Secret 設定在服務上，每個 revision 自動沿用。`--timeout=180` 必須大於 120 秒的 agent 上限。
- **前端**：push 到 `dev` / `main` 且測試通過後，CI 以 Workload Identity Federation 部署到 Hosting（`infra/build_frontend.py` 依環境產生 `config.json` 與 CSP）；手動部署用 `bash infra/deploy_frontend.sh infra/environments/<env>.conf`。
- **監測**：Cloud Run 上的 log 是帶 `severity` 的 JSON，可用 `severity>=ERROR` 篩選；`infra/monitoring.sh` 建立 5xx 與 ERROR log 告警。防冷啟動與存活檢查由外部 ping 服務定期打 `/ping`（間隔 ≤ 10 分鐘）；支援 `GET`（回傳 `{"message":"pong"}`）與 `HEAD`（回傳 `200`、無 body，適用 UptimeRobot）。需要時可用 `UPTIME_CHECK=true` 改建 GCP uptime check。
- 部署後把 API 網址填進 LINE Webhook、Hosting 網址填進 LIFF Endpoint URL，再驗證 `/ping`、LINE Verify、指令查詢，以及聊天室提問 → 開啟 LIFF → 串流回覆。
- 更新 Secret 後要部署新 revision 才會生效；不得在 Cloud Run 設定 `FIRESTORE_EMULATOR_HOST`。建議設定 GCP 與 LLM 供應商的預算告警。

## 開發流程

### 分支

功能分支 → PR 到 `dev`（部署 staging，在 LINE 實測）→ PR `dev` → `main`（部署 prod）。建議在 `main`、`dev` 開啟 branch protection：必須經過 PR 且 CI 通過。

### 依賴管理

`pyproject.toml` + `uv.lock` 是唯一的依賴來源（不使用 requirements.txt）。`uv.lock` 含每個套件的雜湊，**不要手動編輯**：

```powershell
uv add "<套件>>=x,<y"              # 執行期依賴
uv add --group dev <套件>           # 開發工具（格式化、lint）
uv add --group test <套件>          # 只有測試用到的依賴
uv lock --upgrade-package <套件>    # 升級單一套件
```

CI 與 Docker 都用 `uv sync --locked` 安裝：`uv.lock` 與 `pyproject.toml` 不一致就失敗；Docker 只裝執行期依賴（`--no-default-groups`）。Dependabot 每週更新 uv 套件、Docker base image 與 GitHub Actions（小版本合併成一個 PR）。

### 測試

pytest（`asyncio_mode = "auto"`；共用 fixture 在 `tests/conftest.py`，假物件在 `tests/fakes.py`），設定在 `pyproject.toml`。覆蓋率低於門檻（85%，資訊處開發守則的預設值）會失敗。

```powershell
uv run pytest -q --cov
uv run pre-commit run --all-files   # isort、Black（格式）、Ruff（只做 lint）、uv.lock 檢查
```

單元測試不使用雲端金鑰或付費模型（LLM、LINE 驗證、NTHU API 與 Firestore 皆以假物件或記憶體實作取代）。Firestore 整合測試（`@pytest.mark.firestore`）需要 emulator（Firebase CLI + Java 21+）：

```powershell
npx firebase-tools emulators:exec --only firestore --project demo-nthu-chatbot "uv run pytest -q --cov"
```

本機用 emulator 開發時，另開終端執行 `npx firebase-tools emulators:start --only firestore --project demo-nthu-chatbot`，並設 `GOOGLE_CLOUD_PROJECT=demo-nthu-chatbot`、`FIRESTORE_EMULATOR_HOST=127.0.0.1:8085`。

### CI

`.github/workflows/ci.yml` 在每個 PR 與每次 push 到 `main` / `dev` 時執行：`uv sync --locked` → pre-commit（isort、Black、Ruff、uv.lock）→ 在 Firestore emulator 下執行 `pytest --cov`；另一個 job 建置正式映像並確認 `/ping` 會回應（smoke test）。push 時測試通過後再部署前端。
SonarQube Cloud 由學生會從 SonarQube 端直接連結 repo，不在 CI 裡另外跑掃描。
workflow 預設只有 `contents: read`，只有部署 job 能取得 OIDC token；第三方 action 都釘選 commit SHA。

### PR

PR 標題用 `<type>: <description>`，分支用 [conventional branch](https://conventionalbranch.org/)（例如 `fix/retry-duplicates`）。PR 內容依 `.github/pull_request_template.md`：Features / Fixes / Refactors / Internal / Documentations / Notes，沒有的寫 N/A；Validation 只列實際跑過的檢查；介面變更附前後截圖。`CODEOWNERS` 會自動請 maintainer review。

### 版本

`pyproject.toml` 的 `version` 依 [SemVer](https://semver.org/)：修 bug 遞增 PATCH、新功能遞增 MINOR、不相容的變更遞增 MAJOR。

### Commit messages

使用 conventional commits：`feat:`、`fix:`、`docs:`、`style:`、`refactor:`、`perf:`、`test:`、`chore:`、`ci:`、`revert:`；破壞相容性時加 `!` 並在內文寫 `BREAKING CHANGE:`。依元件拆成小 commit，內文說明原因。

## 附錄

### NTHU API v2 遷移

依據 [OpenAPI](https://api.nthusa.tw/openapi.json)（`info.version=2.0.0`）調整：

- 公車：`/buses/info/stops` 使用 `name`；`/buses/stops/{stop_name}` 使用扁平到站欄位，支援 null / 空班次。
- 餐廳：`/dining/` 為建築與餐廳巢狀資料；週末查詢改用 `/dining/open?schedule=...`。
- 圖書館：`/libraries/space` 保留剩餘數量 0；`/libraries/rss/{rss_type}` 處理空頁與 nullable 連結。
- 地圖：使用 `/locations/search?query=...` 模糊搜尋。
- 公告：`/announcements/` 使用 department / language 篩選；舊模組的佈告欄名稱改在本地篩選，處理 nullable 文章欄位。

### 歷史紀錄

舊版程式曾包含硬編碼 Gemini 金鑰。移除檔案不會撤銷金鑰，也不會移除 Git 歷史；請在供應商 Console 撤銷或輪替。
