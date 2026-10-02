# 版本釘選到 digest；Dependabot（docker ecosystem）會提 PR 更新
FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016

# 設定工作目錄
WORKDIR /usr/src/app

# 設定時區，並最小化生成額外檔案的指令
ENV TZ=Asia/Taipei \
    LOGURU_LEVEL=INFO \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080
RUN ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime && echo "$TZ" > /etc/timezone

# 先只複製鎖定檔安裝依賴，程式碼變動時可沿用這層快取
COPY requirements.txt ./

# --require-hashes：每個套件（含間接依賴）都必須符合鎖定檔裡的雜湊
RUN pip install --no-cache-dir --disable-pip-version-check --require-hashes -r requirements.txt

# 將專案程式碼複製到容器
COPY . .

# 暴露應用的默認埠
EXPOSE 8080

# 使用非 root 用戶執行程式以增強安全性
RUN useradd --create-home appuser
USER appuser

# 容器啟動時運行應用
CMD ["python", "main.py"]
