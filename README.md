# 清華校園情報員 · NTHU-Chatbot

清華大學學生會的校園 LINE Bot。聊天室裡用 `@` 指令快速查公車、餐廳、圖書館、地圖與公告；
自由提問則開啟 LIFF 網頁，由 AI 查校園資料後串流回答。

[加入 LINE 好友](https://line.me/R/ti/p/@741vdfol) · [網頁對話](https://chat.nthusa.tw/)

## 功能與技術

- **校園查詢**：`@` 指令直接查 NTHU REST API v2；輸入「說明」、「help」或 `@說明` 查看依 `bot_config.yaml` 產生的指令清單。群組裡只用 `@` 指令，不讀取私人對話。
- **AI 對話**：支援多個對話、串流回答、工具查詢過程與個人化記憶；使用前須同意隱私權政策，可在側欄「我的資料」修改或刪除資料。AI 回覆可能出錯，重要資訊請再確認。
- **登入**：LINE 裡使用 LIFF / LINE Login，一般瀏覽器可用 Auth0；目前兩種登入是不同的內部帳號，對話不互通。
- **介面**：網頁與 LINE Flex 共用 Soft Glass 視覺語言；網頁支援跟隨系統、淺色與深色外觀。

| 層次 | 技術 |
| --- | --- |
| 後端 | FastAPI on Cloud Run（LINE webhook、對話 API、SSE 串流） |
| 前端 | 原生 JS ES modules on Firebase Hosting，無建置工具 |
| AI | OpenAI Agents SDK + NTHU Data MCP（8 個唯讀校園工具）、清大公開 HTTPS 網頁內文讀取；可選擇開啟限定校園網域的網路搜尋 |
| 資料 | Firestore Native mode，由後端服務帳號存取，瀏覽器不直接連線 |

## 快速開始

需要 [uv](https://docs.astral.sh/uv/)；Python 3.14 由 `.python-version` 指定，uv 會自動準備。
在 repo 根目錄執行以下 PowerShell 指令；若已有設定檔，請勿覆蓋：

```powershell
uv sync --locked
uv run pre-commit install
if (-not (Test-Path .env)) { Copy-Item .env.template .env }
if (-not (Test-Path frontend\config.json)) { Copy-Item frontend\config.example.json frontend\config.json }
```

填入 `.env` 的 LINE、LIFF 與 LLM 設定；沒有 GCP 憑證時改用 `CHAT_STORE=memory`（資料不保存），
並設 `CORS_ALLOWED_ORIGINS=http://localhost:5500`。
在 `frontend/config.json` 填入 LIFF ID 與 `apiBase`（本機為 `http://localhost:5000`）。
兩份本機設定都已被 Git 排除，不要提交。

```powershell
uv run main.py                    # API：http://localhost:5000；健康檢查：/ping
```

另開終端啟動前端，再開啟 `http://localhost:5500`：

```powershell
uv run python -m http.server 5500 --directory frontend
```

LINE 裡測試需要公開 HTTPS 網址與 LINE Developers 設定，詳見
[本機開發](docs/development.md#本機開發)與[LINE 設定](docs/configuration.md#line-developers-設定)。

## 文件索引

| 文件 | 內容 |
| --- | --- |
| [開發指南](docs/development.md) | 本機開發、Firestore emulator、依賴管理、測試、CI、PR 與發版 |
| [設定指南](docs/configuration.md) | 環境變數、憑證、LINE / LIFF 設定與 Rich Menu |
| [架構與資料](docs/architecture.md) | 系統資料流、程式分層、身分模型、Firestore 路徑、AI 對話與網頁內文讀取 |
| [隱私權與資安](docs/privacy-security.md) | 同意與政策版本、資料刪除、登入驗證、瀏覽器與 LLM 防護 |
| [校園 API](docs/campus-api.md) | NTHU API v2 端點、資料格式、舊 postback 相容與 MCP 工具遷移 |
| [介面設計](design.md) | 網頁與 LINE Flex 規範、共用主題、預覽與手機端驗收 |
| [草地測試 SOP](docs/grass-testing-sop.md) | 通用草稿：校準、iPhone 連線、現場測試與紀錄；設備專屬流程待補 |
| [部署與維運](infra/README.md) | staging / prod 資源、LINE / Auth0 環境設定、部署腳本、Secret 與監測 |
| [Agent 開發指引](AGENTS.md) | Coding agent 應遵守的專案慣例、修改限制與驗證入口 |

部署流程：合併進 `main` 自動部署 staging；推送版本 tag `vX.Y.Z` 部署 prod，
沿用 staging 驗證過的同一個後端映像。發版步驟見[分支與發版](docs/development.md#分支與發版)。
