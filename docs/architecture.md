# 架構與資料

[回到 README](../README.md) · [設定指南](configuration.md) · [隱私權與資安](privacy-security.md)

## 系統資料流

```text
LINE 聊天室 ──webhook──▶ Cloud Run /callback
                          ├─ @ 指令 → 指令模組（公車 / 餐廳 / 圖書館 / 地圖 / 公告…）→ NTHU REST API v2
                          └─ 其他文字 → 回一顆按鈕，開啟 LIFF 並帶入問題（同一顆按鈕永遠回到同一串對話）

對話網頁（Firebase Hosting：frontend/；LINE 裡以 LIFF 開啟，一般瀏覽器也能用）
   │  CORS · Authorization: Bearer <token> · X-Auth-Provider: line | auth0
   │    LINE 裡：LIFF id_token（LINE Login）
   │    一般瀏覽器：Auth0 access token（Universal Login，Authorization Code + PKCE）
   ▼
Cloud Run /api/* ──▶ AgentRunner（OpenAI Agents SDK）
   │                  ├─ MCP: https://api.nthusa.tw/mcp（唯讀校園工具，結果短暫快取）
   │                  ├─ 個人化工具：save_profile / remember / forget
   │                  ├─ visit_webpage（只讀 nthu.edu.tw 與其子網域的公開 HTTPS 網頁內文）
   │                  └─ nthu_web_search（選用，只搜設定的校園網域）
   ├─ Firestore：users / identityLookup / conversations / messages …
   └─ SSE ──▶ thinking · interim · tool_call_start / end · suggestions · memory · token · done · error
```

- **聊天室只做「reply token 一定來得及」的事**；AI 對話在 LIFF 網頁進行（多個對話、串流、即時顯示正在查什麼）。群組裡只用 `@` 指令，不讀取私人對話。
- **前後端分離**：頁面由 Hosting 的 CDN 提供，頁面的 CSP 設在 Hosting；前端直接以 CORS 呼叫 Cloud Run。不使用 Hosting rewrite 轉到 Cloud Run，因為 rewrite 有 60 秒上限，會切斷較長的 SSE 回覆。
- **身分**：LINE 裡用 LIFF `id_token`（後端向 LINE 驗證）；一般瀏覽器啟用 Auth0 時用簽給這個 API 的 access token（後端以 JWKS 本地驗證）。兩者都對應到內部 user，所有資料只認內部 user id。Auth0 未設定時一般瀏覽器沿用 LIFF 的 LINE 網頁登入。
- 輸入「說明」、「help」或 `@說明` 會回使用說明泡泡（指令清單依 `bot_config.yaml` 自動產生）。
- webhook 等事件處理完才回應；非關鍵寫入（最近活動時間、模組使用次數）與請求並行，回應前收尾，不留會被 Cloud Run 暫停的背景工作。

## 目錄結構

```text
main.py / log.py                  啟動腳本；loguru（Cloud Run 上輸出帶 severity 的 JSON）
bot_config.yaml                   @ 指令模組與中文前綴對照
src/core/config.py                Settings（全部從環境變數讀取，格式不符時啟動失敗）
src/app/__init__.py               create_app()、lifespan 組裝 app.state
src/app/routes/callback.py        LINE webhook
src/app/routes/chat.py            /api/sessions…（LIFF 對話，SSE）
src/app/routes/account.py         /api/me、同意紀錄、個人資料、刪除我的資料
src/app/auth/                     Authenticator（LINE、Auth0）、外部身分 → 內部 user、限流
src/app/middleware.py             API 的安全標頭與 CORS
src/app/handlers/、src/modules/   聊天室內 @ 指令
src/application/                  資料模型；ChatStore / UserStore / ModuleRegistry 介面與記憶體實作；系所名稱正規化
src/infrastructure/ai/            AgentRunner、prompt、個人化工具、網路搜尋、網頁內文、單次執行狀態
src/infrastructure/firebase/      上述介面的 Firestore 實作
templates/messages/               Flex 訊息（含 LIFF 入口泡泡）與共用主題 / builder
frontend/                         LIFF 前端與隱私權政策頁
infra/                            環境建置、監測、前端部署腳本與各環境設定（非機密）
cloudbuild.yaml                   Cloud Build：建置 staging 映像並更新 Cloud Run
cloudbuild.release.yaml           將同一個 commit 的 staging 映像提升為 prod，不重新建置
tests/                            pytest 與 Node 前端測試（Python fixture 在 conftest.py，假物件在 fakes.py）
docs/                             開發、設定、架構、隱私與校園 API 文件
design.md                         網頁與 LINE Flex 設計規範、預覽流程
AGENTS.md                         Coding agent 的專案開發指引
pyproject.toml / uv.lock          依賴（執行期、dev、test group）與工具設定 / uv 產生的鎖定檔
.pre-commit-config.yaml           isort、Black、Ruff 與 uv.lock 檢查（本機與 CI 共用）
```

程式沿用 `src/{app,application,core,infrastructure}` 分層，而不是資訊處開發守則給新專案的
`src/<package>/`：這個 repo 早於守則，搬移會改動所有 import，目前沒有實際好處。
網頁與 LINE Flex 的主題、互動與預覽規範見 [design.md](../design.md)。

## 身分與資料

Firestore Native mode，由 `infra/bootstrap.sh` 建立；staging 用 `(default)`，prod 用 `prod`，
區域建立後不能更換。資料設計原則：
**高頻一起讀的放同一份文件、持續增長的用 subcollection、需要跨 user 查詢的放 top-level、歷史資料保留版本**。

### 內部 user id 與外部登入方式分開

所有資料都以內部 `userId`（`usr_` + 128 bit 隨機值，建立後不可變）為主鍵。
LINE、Auth0 都是連結到 user 的外部身分；之後加學校 OAuth 或 Google，
只要新增一個 `Authenticator`（`src/app/auth/`）並在 `create_app()` 的 lifespan 組裝流程註冊，資料層不用改。

```text
請求 → Authenticator（依 X-Auth-Provider：line，啟用時另有 auth0；未知的一律 401）→ VerifiedIdentity
     → IdentityService：identityLookup/{sha256(provider:id)} → userId
     → 路由、限流、每日額度、所有資料都只認 userId
```

- `identityLookup` 的文件 ID 是雜湊值：學號這類個資不會出現在文件路徑、Console 與 log；原始外部 ID 只存在 `users/{uid}/identities/{provider}`。API 不回傳任何 ID。
- 第一次見到的身分以一次原子寫入建立 user、identity 與 lookup（`create()`，已存在就失敗）；同一個帳號同時進來也只會建立一個 user。
- 帳號連結（之後開放）**只能明確進行**：同一個請求裡同時證明兩個身分，絕不用 email、名稱或學號自動合併；同一個外部身分不能屬於兩個 user（衝突回 409 且不透露是誰）；至少保留一個登入方式；連結與解除都寫入 `auditLog`。
- 目前 Auth0 登入（provider `auth0`）與 LIFF 登入（provider `line`）是不同的內部 user，對話不互通。Auth0 的環境設定與帳號連結限制見 [部署文件](../infra/README.md#auth0一般瀏覽器登入)。
- 群組裡的發言者沒有和 bot 建立關係，webhook 不會為他們建立任何資料。

### Firestore 路徑

| 路徑 | 資料 |
| --- | --- |
| `identityLookup/{sha256(provider:id)}` | `userId`、`provider`、`createdAt` |
| `quotaCarryover/{sha256(provider:id)}` | 刪除帳號時保留當日 AI 用量：`day`、`count`、`expiresAt`（TTL 2 天）。同一個外部身分當天重建帳號時沿用，避免以刪除帳號重置每日上限 |
| `users/{uid}` | `status`（active / blocked / deleting / deleted；deleted 只剩不含個資的墓碑與 `expiresAt` TTL）、`displayName`、`pictureUrl`、`createdAt`、`updatedAt`、`lastActiveAt`、`lastConversationId`、`lastModuleId`、`lastModuleUsedAt`、`conversationCount` |
| `users/{uid}/identities/{provider}` | `providerUserId`、`linkedAt`、`lastLoginAt`、`metadata`（LINE：`followed`、`liff`{os、appVersion、language、contextType、friendshipStatus}；前端自報，不參與授權；不收 contextId） |
| `users/{uid}/consents/{type}_v{version}` | 同意紀錄：`status`（accepted）、`acceptedAt`、`source`；每個版本一份，不覆蓋 |
| `users/{uid}/preferences/{nickname\|department}` | `value`、`source`（user：設定頁；assistant：對話中由 AI 記下）、`updatedAt` |
| `users/{uid}/memory/{m00…m19}` | 使用者要求記住的事：`value`（≤100 字）、`sourceConversationId`、`createdAt`；每則占一個固定格子，以 `create()` 搶空格，並行也不會超過 20 則 |
| `users/{uid}/moduleStates/{moduleId}` | `@` 指令的 `lastUsedAt`、`usageCount`；`onboarding` 記錄首次使用是否已問過稱呼與系所 |
| `users/{uid}/usage/{YYYY-MM-DD}` | 每日 LLM 訊息計數 `count`、`expiresAt`（TTL 8 天） |
| `users/{uid}/auditLog/{id}` | 連結、解除、同意：`action`、`at`、`expiresAt`（TTL 365 天） |
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
- **網路搜尋**（`WEB_SEARCH_ENABLED=true`，預設關閉；需要官方 OpenAI + Responses API；每次搜尋另外計費）：包成我們自己的 `nthu_web_search` function tool，內部以 Responses API 的 web_search 搜尋，`allowed_domains` 只允許 `WEB_SEARCH_DOMAINS`（預設 nthu.edu.tw，含子網域），回傳的來源網址在伺服器端再過濾一次。不直接掛 hosted web search，是為了在搜尋結果進入對話前就能計次並標記「本輪已讀取外部資料」。
- **個人化**：首次同意後會詢問稱呼與系所（可略過）；沒填也沒略過的話，AI 在第一次回答最後問一次。對話中使用者明確說出時，AI 用 `save_profile` / `remember` / `forget` 記下或刪除；系所比對 NTHU API `/directory` 的官方學術單位名稱（只取名稱，不存人員資料）與常見簡稱表，有多個可能時讓使用者選。使用者可在側欄「我的資料」修改或刪除。
- **Prompt 版本**：修改 `src/infrastructure/ai/prompts.py` 的 system prompt 時遞增 `PROMPT_VERSION`；每則回答都會記下版本。

外部工具端點與格式見[校園 API](campus-api.md)，個人化寫入與同意限制見[隱私權與資安](privacy-security.md)。

### 網頁內文讀取

`visit_webpage(url)` 預設提供，Chat Completions 與 Responses API 都可用，不需開啟付費搜尋。
固定只讀取 `nthu.edu.tw` 與其子網域的公開 HTTPS 網頁，不受 `WEB_SEARCH_ENABLED` 或
`WEB_SEARCH_DOMAINS` 影響；相關設定與共用額度見[設定指南](configuration.md#網頁讀取與搜尋)。

使用 Trafilatura 擷取主要內文，去除導覽、頁首頁尾、廣告、留言與 HTML 雜訊，
保留段落、清單與表格文字，不回傳額外 metadata。
內文受 `MAX_TOOL_OUTPUT_CHARS` 限制並標示截斷；不支援 PDF、附件或需登入／JavaScript 的內容，
失敗會明確回報。

下載與解析共用 20 秒上限，下載最多 2 MiB 原始回應資料、3 次重新導向。
解析在獨立程序中執行，逾時或取消時會終止並回收程序，不留下背景解析工作。
呼叫計入 `MAX_TOOL_CALLS_PER_MESSAGE` 的外部工具上限，也會阻擋本輪後續個人化寫入。
prompt 提醒 AI 使用使用者提供或工具查到的確切網址，不送非清大網址、不猜測網址，
也不重複失敗請求；回答時附上讀取的來源連結。
網址、連線與回應的防護見[網頁內文讀取](privacy-security.md#網頁內文讀取)。
