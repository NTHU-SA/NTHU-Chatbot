# 設定指南

[回到 README](../README.md) · [本機開發](development.md#本機開發) · [部署與維運](../infra/README.md)

## 環境變數與憑證

**所有憑證只從環境變數讀取，任何提交的檔案都不得含真實值。**
本機使用 git-ignored 的 `.env`（由 `.env.template` 複製），雲端由 Secret Manager 注入。
`.gitignore`、`.dockerignore`、`.gcloudignore` 均排除 `.env*` 與金鑰檔。
Firestore 不用金鑰檔：Cloud Run 用執行期服務帳號的 ADC，
本機用 `gcloud auth application-default login`。

| 類別 | 變數 |
| --- | --- |
| 機密（Secret Manager） | `LINE_CHANNEL_SECRET`、`LINE_CHANNEL_ACCESS_TOKEN`、`OPENAI_API_KEY` |
| LINE Login / LIFF | `LINE_LOGIN_CHANNEL_ID`、`LIFF_ID`（部署環境不寫在 repo：Cloud Run 環境變數與 repo variable `LIFF_ID_<ENV>`，見 [LINE 頻道設定](../infra/README.md#line-頻道設定不寫在-repo)） |
| Auth0 | `AUTH0_DOMAIN`、`AUTH0_AUDIENCE`、`AUTH0_CLIENT_ID`（三個都填才啟用）；選用 `AUTH0_LINE_CONNECTION`（LIFF 也經 Auth0 的 LINE 連線登入）。Action、環境隔離與 Dashboard 設定見 [Auth0](../infra/README.md#auth0nthusa-id-登入) |
| 前端 | `CORS_ALLOWED_ORIGINS`（逗號分隔；只接受 https 網域，本機可用 `http://localhost:<port>`） |
| LLM | `OPENAI_MODEL`、`OPENAI_BASE_URL`（選填，任何 OpenAI 相容端點）、`OPENAI_USE_RESPONSES_API`、`REASONING_SUMMARY`、`MAX_OUTPUT_TOKENS`、`MAX_OUTPUT_CHARS` |
| MCP | `MCP_SERVER_URL`、`MCP_ALLOWED_TOOLS`（逗號分隔）、`MCP_TIMEOUT_SECONDS` |
| 校園 REST API | `API_ENDPOINT`（聊天室內的 `@` 指令模組使用，預設 `https://api.nthusa.tw`） |
| 工具與搜尋 | `MAX_TOOL_CALLS_PER_MESSAGE`、`WEB_SEARCH_ENABLED`、`WEB_SEARCH_MODEL`、`WEB_SEARCH_DOMAINS`、`MAX_WEB_SEARCHES_PER_MESSAGE` |
| 隱私權 | 政策版本寫在 `src/core/privacy.py`（不是環境變數；改版時連同 `frontend/privacy.html` 一起改） |
| 儲存 | `CHAT_STORE`（`firestore` / `memory`）、`GOOGLE_CLOUD_PROJECT`、`FIRESTORE_DATABASE`（staging 用 `(default)`，prod 用 `prod`） |
| 本機 emulator | `FIRESTORE_EMULATOR_HOST`（僅本機使用，不得設在 Cloud Run） |
| 執行環境 | `DEV_MODE`、`PORT` |
| 限制 | `HISTORY_WINDOW`、`HISTORY_MESSAGE_CHARS`、`MAX_TOOL_OUTPUT_CHARS`、`MAX_MESSAGE_CHARS`、`DAILY_MESSAGE_LIMIT`、`MAX_AGENT_TURNS`、`TOOL_RESULT_PREVIEW_CHARS` |

每個變數的說明與預設值見 [.env.template](../.env.template)，解析與驗證在 `src/core/config.py`；
格式不符時啟動失敗。
前端本機設定由 `frontend/config.example.json` 複製為 git-ignored 的 `frontend/config.json`。
部署時由 `infra/build_frontend.py` 產生公開設定與 CSP，詳見[部署與維運](../infra/README.md)。

## 網頁讀取與搜尋

`visit_webpage` 與 `nthu_web_search` 是不同工具，前者讀取已知網址的主要內文，後者查詢搜尋結果：

| 工具 | 啟用與範圍 | 共用限制 |
| --- | --- | --- |
| `visit_webpage` | 預設提供，Chat Completions 與 Responses API 都可用；固定只讀 `nthu.edu.tw` 與其子網域的公開 HTTPS HTML，不受 `WEB_SEARCH_ENABLED` / `WEB_SEARCH_DOMAINS` 影響 | 呼叫計入 `MAX_TOOL_CALLS_PER_MESSAGE`，內文受 `MAX_TOOL_OUTPUT_CHARS` 限制 |
| `nthu_web_search` | `WEB_SEARCH_ENABLED=true` 且使用官方 OpenAI + Responses API 才可用；網域由 `WEB_SEARCH_DOMAINS` 限定（預設 nthu.edu.tw），搜尋另計費 | 計入 `MAX_TOOL_CALLS_PER_MESSAGE`，另受 `MAX_WEB_SEARCHES_PER_MESSAGE` 限制 |

`visit_webpage` 沒有獨立的啟用或網域環境變數；不要以搜尋設定放寬其範圍。
內容格式、逾時、下載與解析上限見[網頁內文讀取](architecture.md#網頁內文讀取)。

## LINE Developers 設定

同一個 **Provider** 底下建立兩個 channel（不同 Provider 的 userId 不同，同一個人會變成兩個 user）：

1. **Messaging API channel**：Channel secret → `LINE_CHANNEL_SECRET`；長期 Channel access token → `LINE_CHANNEL_ACCESS_TOKEN`。
   Webhook URL 設為 `https://<API 網址>/callback`，啟用 Webhook，停用衝突的自動回應與歡迎訊息。
2. **LINE Login channel**：Channel ID → `LINE_LOGIN_CHANNEL_ID`。新增 **LIFF app**：
   Endpoint URL `https://<HOSTING_SITE>.web.app/`、Size `Full`、Scopes 勾選 `profile` 與 **`openid`**（缺少就拿不到 id_token）。
   LIFF ID → `LIFF_ID`。在 LINE 以外的瀏覽器測試時，要把自己加入 Login channel 的 tester 或發佈 channel。

staging / prod 各自注入頻道 ID 與金鑰，不寫進 repo；更換頻道時前後端要一起換，
否則 LIFF 的 id_token 會因 channel ID 不符被拒絕。各環境的設定位置見
[LINE 頻道設定](../infra/README.md#line-頻道設定不寫在-repo)。

## Rich Menu

Rich Menu 不在啟動時建立；更新選單請執行：

```powershell
uv run python -m scripts.rich_menu
```

需要該環境的 `LINE_CHANNEL_ACCESS_TOKEN` 與 `LIFF_ID`（環境變數或 `.env`）。
**這會取代現有選單**，執行前確認 `data/richmenu/menu-main.png`、`data/richmenu/menu-more.png` 都正確。
