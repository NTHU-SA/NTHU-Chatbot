# Agent 開發指引

本文件適用於整個 repository，提供 coding agent 修改程式時的專案慣例。
使用者入口在 [README.md](README.md)，詳細操作說明按任務閱讀，不必把整份文件當成 prompt。

## 專案與文件導航

此專案是清華大學學生會的 LINE 校園助理：
FastAPI 後端、原生 JS / LIFF 前端、OpenAI Agents SDK、NTHU Data MCP 與 Firestore。

| 任務 | 先讀文件 | 主要程式位置 |
| --- | --- | --- |
| 本機開發、依賴、測試、PR | [開發指南](docs/development.md) | `pyproject.toml`、`uv.lock`、`tests/`、`.github/workflows/ci.yml` |
| 環境變數、LINE / LIFF、Rich Menu | [設定指南](docs/configuration.md) | `src/core/config.py`、`.env.template`、`scripts/rich_menu.py` |
| 路由、登入、資料儲存、AI | [架構與資料](docs/architecture.md) | `src/app/`、`src/application/`、`src/infrastructure/` |
| AI 網頁內文與搜尋工具 | [網頁內文讀取](docs/architecture.md#網頁內文讀取)、[設定指南](docs/configuration.md#網頁讀取與搜尋) | `src/infrastructure/ai/webpage.py`、`webpage_parser.py`、`web_search.py` |
| 同意、個人化、資料刪除 | [隱私權與資安](docs/privacy-security.md) | `src/core/privacy.py`、`src/app/routes/account.py`、`frontend/privacy.html` |
| 校園查詢、API 格式 | [校園 API](docs/campus-api.md) | `src/modules/`、`src/utils/nthuapi.py`、`src/infrastructure/ai/` |
| 網頁或 LINE Flex | [介面設計](design.md) | `frontend/`、`templates/messages/`、`scripts/build_flex_preview.py` |
| 部署、環境隔離、Secret、監測 | [部署與維運](infra/README.md) | `infra/`、`cloudbuild.yaml`、`cloudbuild.release.yaml` |

## 修改慣例

- 保留既有 `src/{app,application,core,infrastructure}` 分層，不為符合新專案目錄慣例而搬移整個 repo。資料模型與儲存介面在 `src/application/`；記憶體與 Firestore 實作須維持相同行為。
- Python 版本由 `.python-version` 指定（3.14）。依賴只用 `pyproject.toml` + `uv.lock`；用 `uv add` / `uv lock` 更新，禁止手動編輯鎖定檔或新增另一套 requirements.txt。
- 格式使用 Black（行寬 100），import 順序交給 isort（black profile），Ruff 只做 lint，不當第二個 formatter。設定在 `pyproject.toml`，本機與 CI 共用 `.pre-commit-config.yaml`。
- 前端是原生 JS ES modules，無建置工具；沿用現有 CSS tokens、頁內 SVG 與共用元件，不為一般介面修改新增 UI 套件或字型下載。
- 修改 system prompt 時同步遞增 `src/infrastructure/ai/prompts.py` 的 `PROMPT_VERSION`。修改隱私權政策或蒐集範圍時，同步更新 `frontend/privacy.html` 與 `src/core/privacy.py` 的 `PRIVACY_POLICY_VERSION`。
- 保留既有 LINE URI、postback、message、Quick Reply 與分頁行為，尤其舊 postback 的相容轉換；校園資料格式與公車路線規則見[校園 API](docs/campus-api.md)。
- 修改行為時更新直接相關的文件與測試，不將詳細維運或設計說明重新塞回 README。

## 不可破壞的邊界

- 憑證只從環境變數讀取；本機 `.env`、`frontend/config.json`、金鑰與真實 token 不得提交。雲端用 Secret Manager 與服務帳號 ADC，不新增服務帳號金鑰檔。
- 所有資料、限流與每日額度只認內部 `userId`，不可改用 LINE ID 或 Auth0 `sub` 當資料主鍵；不得靠 email、名稱或學號自動合併帳號。
- LINE 與 Auth0 使用各自驗證過的 token。未知 provider 與無效 token 回 401；讀取對話須檢查擁有者，不向前端洩漏內部身分或分析欄位。
- AI 對話須由後端強制同意目前政策版本；同一輪讀過外部資料後禁止個人化寫入。保留工具呼叫、逾時、輸出長度與每日用量限制，公車即時資料不快取。
- `visit_webpage` 固定只讀清大公開 HTTPS 網頁，不隨 `WEB_SEARCH_DOMAINS` 放寬。保留重新導向與公開 IP 驗證、連線 IP 固定、TLS 驗證、未壓縮回應檢查，以及下載／解析上限與獨立解析程序的逾時清理；呼叫仍計入外部工具額度並阻擋本輪個人化寫入。
- 保留刪除流程的 `deleting` / `deleted` 狀態、重試清理與當日額度 carryover，避免並行寫入留下資料或刪除帳號重置額度。
- log 不記訊息內容、token 或任何 ID；例外只記型別名稱。不可讓 Firestore rules 開放瀏覽器直接存取，亦不可放寬 CORS / CSP 以繞過設定問題。
- AI 對話在網頁以 SSE 執行，不移入 webhook；前端直接以 CORS 呼叫 Cloud Run，不改成 Hosting rewrite（60 秒上限會截斷串流）。webhook 的並行寫入必須在回應前收尾。
- staging / prod 的資料庫、Secret、服務帳號、WIF 與服務保持隔離；不得在 Cloud Run 設 `FIRESTORE_EMULATOR_HOST`。未被要求時不執行部署、Secret 輪替或取代 Rich Menu 的操作。

## 網頁與 Flex 修改

- 網頁顏色、字型、字級、間距與圓角使用 `frontend/style.css` 的 CSS 變數；深色模式以 `data-theme` 覆寫。保留 `prefers-reduced-motion`、可存取標籤、對比與 AI 回覆提醒。
- LINE Flex 的全域設定集中在 `templates/messages/flex_theme.py` 的 `FLEX_THEME`，沿用共用 Python / Jinja builder，不重複寫色碼、字級、間距，也不將網站 CSS 單位直接送給 LINE。
- 修改 builder 或模板後執行 `uv run python scripts\build_flex_preview.py`，並一起提交產生的 `frontend/flex-preview-data.js`。預覽只用假資料，Hosting 排除 `flex-preview*`；上線前仍需手機 LINE 驗收。

## 驗證與交付

按改動範圍執行相關測試；單元測試使用 `tests/conftest.py` / `tests/fakes.py`、假物件或記憶體實作，
不得使用雲端金鑰或付費模型驗證一般單元測試。

```powershell
uv run pytest -q --cov
uv run pre-commit run --all-files
node --test tests\frontend_theme.test.cjs tests\frontend_navigation.test.cjs tests\frontend_ui.test.cjs tests\frontend_flex_preview.test.cjs tests\auth0_action.test.cjs
npx firebase-tools emulators:exec --only firestore --project demo-nthu-chatbot "uv run pytest -q --cov"
```

Firestore 整合測試需要 Firebase CLI 與 Java 21+；pytest 覆蓋率門檻為 85%，
完整 CI 與 Flex 的相關測試指令見[開發指南](docs/development.md#測試)與[設計規範](design.md#預覽與維護)。
只修改文件時不需跑程式測試，但應確認相對連結與章節索引仍有效。

Commit / PR 標題使用 conventional commits；PR 必須保留
[PR template](.github/pull_request_template.md) 的章節，沒有的填 N/A，Validation 只列實際執行過的檢查與結果。
版本只在發版時遞增，prod tag 必須對應 `pyproject.toml` 的 version 與已在 staging 驗證過的 commit；
流程見[分支與發版](docs/development.md#分支與發版)。
