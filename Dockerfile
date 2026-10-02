# 版本釘選到 digest；Dependabot（docker ecosystem）會提 PR 更新
FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016

# uv 只用來依 uv.lock 安裝依賴（同樣釘 digest）
COPY --from=ghcr.io/astral-sh/uv:0.12.22@sha256:f513a91fc62fe7c17567eee97230dd198e43edb8a9fbecca843714a4358fe1bc /uv /usr/local/bin/uv

# 設定工作目錄
WORKDIR /usr/src/app

# 設定時區，並最小化生成額外檔案的指令
ENV TZ=Asia/Taipei \
    LOGURU_LEVEL=INFO \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH"
RUN ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime && echo "$TZ" > /etc/timezone

# 先只複製鎖定檔安裝依賴，程式碼變動時可沿用這層快取
COPY pyproject.toml uv.lock ./

# --locked：uv.lock 與 pyproject.toml 不一致就失敗；每個套件都會比對鎖定檔裡的雜湊。
# 只裝執行期依賴（不含 dev / test group）
RUN uv sync --locked --no-default-groups --no-cache

# 將專案程式碼複製到容器
COPY . .

# 暴露應用的默認埠
EXPOSE 8080

# 使用非 root 用戶執行程式以增強安全性
RUN useradd --create-home appuser
USER appuser

# 容器啟動時運行應用
CMD ["python", "main.py"]
