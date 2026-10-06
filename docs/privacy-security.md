# 隱私權與資安

[回到 README](../README.md) · [架構與資料](architecture.md#身分與資料) · [部署與維運](../infra/README.md)

## 隱私權

- 隱私權政策在 `frontend/privacy.html`（**草稿，需學生會審閱定稿並填入聯絡方式**），依個資法第 8 條列出蒐集者、目的、資料類別、期間 / 地區 / 對象（含 OpenAI 美國）、當事人權利與不提供的影響。
- **後端強制同意**：未同意目前版本時，送出訊息回 `403 {"code": "consent_required", "version": …}`，內容不會送到 LLM；`@` 指令不經過 AI，不需要同意。
- **政策版本只有一個來源**：`src/core/privacy.py`。後端以它決定要同意哪一版；`infra/build_frontend.py` 把同一個值寫進 `privacy.html` 的版本與 `config.json`。前端送出同意時帶的是畫面上顯示的版本，前後端尚未同步部署時後端回 409，前端請使用者稍後再開，不會記錄成沒看過的版本。改版時修改政策內容並遞增版本，舊版本的紀錄保留。
- 使用者可在側欄**刪除我的所有資料**（`DELETE /api/me`：對話、訊息、個人化資料、同意與使用紀錄、外部身分對應與帳號本身全部刪除；之後同一個 LINE 帳號是全新的使用者）。不提供撤回同意按鈕或 API；尚未同意、舊版本同意與歷史上未接受的紀錄仍不能使用 AI 對話。
- **刪除流程**：先把帳號標成 `deleting`（其他請求一律 403，只能再呼叫刪除）→ 刪除對話與 user 資料，每一步重新查詢確認清空 → user 文件換成不含個資的墓碑（`status=deleted`，`expiresAt` TTL 1 天）→ 再清一次。墓碑之後才完成的寫入會讀到 `deleted` 並撤銷自己，所以刪除與進行中的請求同時發生也不會留下資料。沒刪乾淨時回 `503 deletion_incomplete`，帳號維持 `deleting`，使用者重新開啟頁面會看到「完成刪除」的按鈕。
- 刪除帳號不會重置當日 AI 額度：`quotaCarryover` 保留當日用量、TTL 2 天，同一外部身分重建帳號時沿用。
- LLM 供應商會收到最近 `HISTORY_WINDOW` 則對話與使用者資料區塊；MCP 只收到模型產生的查詢參數。Agents SDK 的 tracing 已停用。

## 資安重點

- **登入**：只採用 provider 驗證過的身分。LINE：verify endpoint 檢查 `aud`、`iss`。Auth0：只接受 RS256，檢查簽章（JWKS）、`iss`、`aud`（每個環境一個 API）、`exp`，以及 `azp` 必須是 chat 自己的 Application（同一個 tenant 的其他 Application 拿到的 token 不被接受）；前端不用 id_token 呼叫 API。顯示名稱與頭像只來自驗證過的 claims 或 `/userinfo`；未知的 provider 與無效 token 回同樣的 401。
- **瀏覽器的 Auth0 token**：refresh token 只放在同源 Web Worker 的記憶體（不存 localStorage），重新整理後以 Auth0 網域的隱藏 iframe 靜默取得；登入後只導回同源路徑，網址列不留 `code` / `state`。
- **API**：CORS 只允許設定的前端網域且不帶 cookie；API 回應帶 `default-src 'none'`、`X-Frame-Options: DENY`；`docs_url` 等文件端點關閉。
- **前端**：Hosting 設定嚴格 CSP（`connect-src` 只允許該環境的 API、LINE 與 Auth0 網域）；模型輸出經 DOMPurify 消毒，連結只允許 http(s) / mailto。
- **防 prompt injection**：同一輪只要讀過外部資料（任何 MCP 工具結果、網路搜尋或網頁內文），個人化寫入工具一律拒絕；寫入的文字去掉換行與角括號、限制長度，注入 instructions 時包在 `<user_profile>` 並標明「不是指令」；工具結果在 prompt 中也被標示為資料。
- **資料**：Firestore rules 全部拒絕，只有後端服務帳號能存取；執行期與部署服務帳號都是最小權限（見[部署文件](../infra/README.md)）；log 只記例外型別名稱，不記內容、token 或任何 ID。
- **供應鏈**：依賴以雜湊鎖定（`--require-hashes`）、Docker base image 釘 digest、GitHub Actions 釘 commit SHA；CI 以 Workload Identity Federation 部署，不存任何 GCP 金鑰。

### 網頁內文讀取

`visit_webpage` 固定只允許 `nthu.edu.tw` 與其子網域的公開 HTTPS 網頁，
逐次檢查重新導向並拒絕內網或非公開位址。
連線固定到驗證過的 IP，同時保留原 Host 與 TLS 網域驗證，避免 DNS rebinding。
搜尋的 `WEB_SEARCH_DOMAINS` 不會放寬這個工具的範圍。

下載要求未壓縮回應，並拒絕壓縮內容，避免在檢查原始回應大小前解壓縮。
下載與解析的時間、大小與程序清理限制見[架構指南](architecture.md#網頁內文讀取)。
呼叫在讀取前就計入外部工具額度並標記本輪不可個人化寫入，
網頁文字只能當作資料，不能當成指令。

## 歷史金鑰提醒

舊版程式曾包含硬編碼 Gemini 金鑰。移除檔案不會撤銷金鑰，也不會移除 Git 歷史；
請在供應商 Console 撤銷或輪替。現行 Secret 的新增與輪替步驟見
[加入 Secret 值](../infra/README.md#加入-secret-值)。
