# 架構與資料

[回到 README](../README.md) · [設定指南](configuration.md) · [隱私權與資安](privacy-security.md)

## 系統資料流

```text
LINE 聊天室 ──webhook──▶ Cloud Run /callback
                          ├─ @ 指令 → 指令模組（公車 / 餐廳 / 圖書館 / 地圖 / 公告…）→ NTHU REST API v2
                          └─ 其他文字 → 回一顆按鈕，開啟 LIFF 並帶入問題（同一顆按鈕永遠回到同一串對話）

對話網頁（Firebase Hosting：frontend/；LINE 裡以 LIFF 開啟，一般瀏覽器也能用）
   │  CORS · Authorization: Bearer <Auth0 access token> · X-Auth-Provider: auth0
   │    LINE 裡：直接帶到 Auth0 的 LINE 連線；一般瀏覽器：Universal Login
   │    （都是 Authorization Code + PKCE；token 帶 NTHUSA ID）
   ▼
Cloud Run /api/* ──▶ AgentRunner（OpenAI Agents SDK）
   │                  ├─ MCP: https://api.nthusa.tw/mcp（唯讀校園工具，結果短暫快取）
   │                  ├─ 個人化工具：save_profile / remember / forget
   │                  ├─ visit_webpage（只讀 nthu.edu.tw 與其子網域的公開 HTTPS 網頁內文）
   │                  └─ nthu_web_search（選用，只搜設定的校園網域）
   ├─ Firestore：users / identityLookup / conversations / messages / usageStats …
   └─ SSE ──▶ thinking · interim · tool_call_start / end · suggestions · memory · token · done · error
```

- **聊天室只做「reply token 一定來得及」的事**；AI 對話在 LIFF 網頁進行（多個對話、串流、即時顯示正在查什麼）。群組裡只用 `@` 指令，不讀取私人對話。
- **前後端分離**：頁面由 Hosting 的 CDN 提供，頁面的 CSP 設在 Hosting；前端直接以 CORS 呼叫 Cloud Run。不使用 Hosting rewrite 轉到 Cloud Run，因為 rewrite 有 60 秒上限，會切斷較長的 SSE 回覆。
- **身分**：登入只有 Auth0 一條路（LINE 裡也是），access token 簽給這個 API，後端以 JWKS 本地驗證。身分是 token 裡的 NTHUSA ID，所有資料都以它為 key。聊天室不需要登入：LINE userId 綁定了 NTHUSA ID 才對得到人，沒綁定的人不會有任何個人資料。
- 輸入「說明」、「help」或 `@說明` 會回使用說明泡泡（指令清單依 `bot_config.yaml` 自動產生）。
- webhook 等事件處理完才回應；非關鍵寫入（聊天室每天的匿名總數）與請求並行，回應前收尾，不留會被 Cloud Run 暫停的背景工作。

## 目錄結構

```text
main.py / log.py                  啟動腳本；loguru（Cloud Run 上輸出帶 severity 的 JSON）
bot_config.yaml                   @ 指令模組與中文前綴對照
src/core/config.py                Settings（全部從環境變數讀取，格式不符時啟動失敗）
src/app/__init__.py               create_app()、lifespan 組裝 app.state
src/app/routes/callback.py        LINE webhook
src/app/routes/chat.py            /api/sessions…（LIFF 對話，SSE）
src/app/routes/account.py         /api/me、同意紀錄、個人資料、刪除我的資料
src/app/auth/                     Authenticator（Auth0）、NTHUSA ID → user、LINE 綁定的對應、限流
src/app/middleware.py             API 的安全標頭與 CORS
src/app/handlers/、src/modules/   聊天室內 @ 指令
src/application/                  資料模型；ChatStore / UserStore / ModuleRegistry / UsageStats 介面與記憶體實作；系所名稱正規化
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

### NTHUSA ID 就是 user id

所有資料都以 NTHUSA ID 為主鍵（`users/{NTHUSA ID}`）：post-login Action 產生的 `usr_` + ULID，
學生會跨系統共用、staging 與 prod 相同，建立後不會變。它只來自驗證過的 Auth0 access token，
不是 Auth0 的 `sub`（連結帳號時會變）。LINE、Google 等登入方式都在 Auth0 裡，綁定也在 Auth0 進行
（介面在後續 PR）；之後加別的登入方式，資料層不用改。

```text
請求 → Auth0Authenticator（X-Auth-Provider: auth0；未知的一律 401）→ NTHUSA ID（+ 綁定的 LINE userId）
     → IdentityService：users/{NTHUSA ID}（沒有就建立），LINE userId 同步到 identityLookup
     → 路由、限流、每日額度、所有資料都只認 NTHUSA ID

webhook → LINE userId → identityLookup/{sha256(line:U…)} → NTHUSA ID（只查詢，沒有就是 None）
```

- 第一次登入以一次原子寫入建立 user 與它的 `identities/nthusa`（`create()`，已存在就失敗）；同一個 ID 同時進來也只會建立一次。
- token 帶 NTHUSA ID 綁定的 LINE userId（`https://nthusa.tw/line_user_id`）時，以 Auth0 為準同步 `identityLookup`：綁了就對應過來（原本屬於別的 user 就從那邊移除，對方的資料不動），換綁取代，解除就移除；每次變動寫入 `auditLog`。同一個綁定每個實例 10 分鐘只同步一次。
- `identityLookup` 的文件 ID 是雜湊值：LINE userId 不會出現在文件路徑、Console 與 log；原始值只存在 `users/{uid}/identities/line`。API 不回傳任何 ID。
- 聊天室（webhook）只查詢、不建立：沒有綁定的 LINE 使用者照常使用指令，但不寫入任何個人資料；群組裡的發言者連查詢都不做。使用量只記每天的匿名總數（`usageStats`）。
- 刪除資料後，同一個 NTHUSA ID 要過 `REUSE_AFTER_DELETION`（10 分鐘）才能重新開始（期間回 403 `account_deleted`）：刪除前已在進行的請求可能晚到，它們靠讀到 deleted 自己撤銷；太快在同一個 key 重建，會讓它們留在新帳號裡。
- Auth0 的環境設定見 [部署文件](../infra/README.md#auth0nthusa-id-登入)。

### Firestore 路徑

| 路徑 | 資料 |
| --- | --- |
| `identityLookup/{sha256(line:U…)}` | 綁定的 LINE → `userId`（NTHUSA ID）、`provider`、`createdAt` |
| `quotaCarryover/{sha256(nthusa:usr_…)}` | 刪除帳號時保留當日 AI 用量：`day`、`count`、`expiresAt`（TTL 2 天）。同一個 NTHUSA ID 當天重建帳號時沿用，避免以刪除帳號重置每日上限 |
| `users/{uid}` | uid 是 NTHUSA ID。`status`（active / blocked / deleting / deleted；deleted 只剩不含個資的墓碑：`deletedAt`、`expiresAt` TTL）、`displayName`、`pictureUrl`、`createdAt`、`updatedAt`、`lastActiveAt`、`lastConversationId`、`conversationCount` |
| `users/{uid}/identities/{nthusa\|line}` | `providerUserId`、`linkedAt`、`lastLoginAt`、`metadata`（nthusa：`liff`{os、appVersion、language、contextType、friendshipStatus}，前端自報，不參與授權；不收 contextId） |
| `users/{uid}/consents/{type}_v{version}` | 同意紀錄：`status`（accepted）、`acceptedAt`、`source`；每個版本一份，不覆蓋 |
| `users/{uid}/preferences/{nickname\|department}` | `value`、`source`（user：設定頁；assistant：對話中由 AI 記下）、`updatedAt` |
| `users/{uid}/memory/{m00…m19}` | 使用者要求記住的事：`value`（≤100 字）、`sourceConversationId`、`createdAt`；每則占一個固定格子，以 `create()` 搶空格，並行也不會超過 20 則 |
| `users/{uid}/moduleStates/onboarding` | 首次使用是否已問過稱呼與系所 |
| `users/{uid}/usage/{YYYY-MM-DD}` | 每日 LLM 訊息計數 `count`、`expiresAt`（TTL 8 天） |
| `users/{uid}/auditLog/{id}` | LINE 綁定、解除、同意：`action`、`at`、`expiresAt`（TTL 365 天） |
| `users/{uid}/conversationOrigins/{sha256(origin)}` | LINE 泡泡 → `conversationId` |
| `users/{uid}/conversationCleanup/{cid}` | 已刪除、訊息尚未清完的對話（中斷後下次繼續清） |
| `conversations/{cid}` | `userId`、`channel`、`status`、`title`、`startedAt`、`lastMessageAt`、`messageCount`、`metadata.origin`；每人最多 50 個，超過時刪除最久未更新的 |
| `conversations/{cid}/messages/{mid}` | `role`、`contentType`、`content`、`toolCalls`、`model`、`promptVersion`、`tokenUsage`、`latencyMs`、`createdAt` |
| `usageStats/{YYYY-MM-DD}` | 聊天室每天的匿名總數：`messages`、`postbacks`、`follows`、`unfollows`、`modules`{moduleId}；不含任何使用者識別資料 |
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
