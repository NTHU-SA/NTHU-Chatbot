# 開發指南

[回到 README](../README.md) · [設定指南](configuration.md) · [部署與維運](../infra/README.md)

## 本機開發

需要 [uv](https://docs.astral.sh/uv/)；Python 版本寫在 `.python-version`（3.14，與 `pyproject.toml`、Dockerfile、CI 一致），uv 會自動準備。
在 repo 根目錄執行以下 PowerShell 指令；若已有設定檔，請勿覆蓋：

```powershell
uv sync --locked                  # 依 uv.lock 建立 .venv（含 dev 與 test group）
uv run pre-commit install         # 之後每次 commit 自動跑 isort、Black、Ruff
if (-not (Test-Path .env)) { Copy-Item .env.template .env }  # 填入 LINE、LIFF、LLM 設定
```

沒有 GCP 憑證時在 `.env` 設 `CHAT_STORE=memory`（資料不會保存）；要連 Firestore 則先執行
`gcloud auth application-default login`，並設定 `GOOGLE_CLOUD_PROJECT` 與該環境的 `FIRESTORE_DATABASE`。

```powershell
uv run main.py                    # API 在 http://localhost:5000，健康檢查 /ping
```

前端是純靜態檔，在另一個終端執行：

```powershell
if (-not (Test-Path frontend\config.json)) { Copy-Item frontend\config.example.json frontend\config.json }  # 填入 LIFF ID 與 API 網址（git-ignored）
uv run python -m http.server 5500 --directory frontend
```

並在 `.env` 設 `CORS_ALLOWED_ORIGINS=http://localhost:5500`。在 LINE 裡測試需要公開的 HTTPS 網址：
用 `ngrok http 5000` 取得 API 網址，填到 Webhook URL（`/callback`）與 `config.json` 的 `apiBase`；
LIFF Endpoint URL 要指向能公開存取的前端（另一個 ngrok 指向 5500，或直接部署到 staging）。
頻道與 scopes 見 [LINE Developers 設定](configuration.md#line-developers-設定)。

## 依賴管理

`pyproject.toml` + `uv.lock` 是唯一的依賴來源（不使用 requirements.txt）。
`uv.lock` 含每個套件的雜湊，**不要手動編輯**：

```powershell
uv add "<套件>>=x,<y"              # 執行期依賴
uv add --group dev <套件>           # 開發工具（格式化、lint）
uv add --group test <套件>          # 只有測試用到的依賴
uv lock --upgrade-package <套件>    # 升級單一套件
```

CI 與 Docker 都用 `uv sync --locked` 安裝：`uv.lock` 與 `pyproject.toml` 不一致就失敗；
Docker 只裝執行期依賴（`--no-default-groups`）。
Dependabot 每週更新 uv 套件、Docker base image 與 GitHub Actions（小版本合併成一個 PR）。

## 測試

pytest（`asyncio_mode = "auto"`；共用 fixture 在 `tests/conftest.py`，假物件在 `tests/fakes.py`），
設定在 `pyproject.toml`。覆蓋率低於門檻（85%，資訊處開發守則的預設值）會失敗。

```powershell
uv run pytest -q --cov
uv run pre-commit run --all-files   # isort、Black（格式）、Ruff（只做 lint）、uv.lock 檢查
node --test tests\frontend_theme.test.cjs tests\frontend_navigation.test.cjs tests\frontend_ui.test.cjs tests\frontend_flex_preview.test.cjs
```

前端測試使用 Node，無額外套件相依；CI 執行 `tests/*.test.cjs` 的全部測試。
單元測試不使用雲端金鑰或付費模型（LLM、LINE 驗證、NTHU API 與 Firestore 皆以假物件或記憶體實作取代）。
Firestore 整合測試（`@pytest.mark.firestore`）需要 emulator（Firebase CLI + Java 21+）：

```powershell
npx firebase-tools emulators:exec --only firestore --project demo-nthu-chatbot "uv run pytest -q --cov"
```

本機用 emulator 開發時，另開終端執行
`npx firebase-tools emulators:start --only firestore --project demo-nthu-chatbot`，
並設 `CHAT_STORE=firestore`、`GOOGLE_CLOUD_PROJECT=demo-nthu-chatbot`、`FIRESTORE_EMULATOR_HOST=127.0.0.1:8085`。
不得在 Cloud Run 設定 `FIRESTORE_EMULATOR_HOST`。

## CI

`.github/workflows/ci.yml` 在每個 PR、每次 push 到 `main` 與每個版本 tag 執行：
`uv sync --locked` → pre-commit（isort、Black、Ruff、uv.lock）→ 前端 Node 測試
（`node --test tests/*.test.cjs`）→ 在 Firestore emulator 下執行 `pytest --cov`；
另一個 job 建置正式映像並確認 `/ping` 會回應（smoke test）。
push 時測試通過後再部署前端（`main` → staging、tag → prod）。

SonarQube Cloud 由學生會從 SonarQube 端直接連結 repo，不在 CI 裡另外跑掃描。
workflow 預設只有 `contents: read`，只有部署 job 能取得 OIDC token；第三方 action 都釘選 commit SHA。

## PR 與 commit

PR 標題用 `<type>: <description>`，分支用 [conventional branch](https://conventionalbranch.org/)
（例如 `fix/retry-duplicates`）。PR 內容依 [PR template](../.github/pull_request_template.md)：
Features / Fixes / Refactors / Internal / Documentations / Notes，沒有的寫 N/A；
Validation 只列實際跑過的檢查；介面變更附前後截圖。
`CODEOWNERS` 會自動請 maintainer review。合併一律用 squash merge，PR 標題就是 `main` 上的 commit 訊息。

使用 conventional commits：`feat:`、`fix:`、`docs:`、`style:`、`refactor:`、`perf:`、
`test:`、`chore:`、`ci:`、`revert:`；破壞相容性時加 `!` 並在內文寫 `BREAKING CHANGE:`。
PR 分支內依元件拆成小 commit、內文說明原因方便 review；合併時會 squash 成一個，
所以 PR 標題也要符合這個格式。

## 分支與發版

只有一個長期分支 `main`；staging 與 prod 用版本 tag 區分，不再用分支區分。

1. 功能分支 → PR 到 `main`，一律 **squash merge**（一個 PR 在 `main` 上就是一個 commit，標題沿用 PR 標題）。
2. 合併後自動部署 staging，在 LINE 實測。
3. 要上 prod 時，開一個 PR 把 `pyproject.toml` 的 `version` 改成新版本並合併，等 staging 部署完成、確認沒問題後，在該 commit 打 tag 並推送：

   ```bash
   git switch main && git pull
   git tag -a v0.2.0 -m "v0.2.0"
   git push origin v0.2.0
   ```

4. tag 觸發 prod 部署（API 與前端），沿用 staging 驗證過的同一個映像。

`pyproject.toml` 的 `version` 依 [SemVer](https://semver.org/)：
修 bug 遞增 PATCH、新功能遞增 MINOR、不相容的變更遞增 MAJOR。
只在發版時遞增，並打同名的 tag（`version = "0.2.0"` ↔ `v0.2.0`）；prod 部署會檢查兩者一致。

`main` 有 branch protection（必須經過 PR、1 個 approval、CI 通過、不能 force push 或刪除）；
`v*` tag 有 tag ruleset，只有管理員能建立、更新或刪除，避免沒經過 review 的 commit 被打 tag 上 prod。

回滾：在 Cloud Run 把流量切回前一個 revision，或對舊 tag 重跑 prod trigger
（`gcloud builds triggers run deploy-nthu-chatbot --tag=v0.1.0 --region=global`）。
版本 tag 的映像會一直保留；staging 映像只保留最新 10 個，其餘 7 天後刪除，
所以要發版的 commit 請在合併後一週內打 tag（過期的話對該 commit 重跑一次 staging trigger 即可重建）。
