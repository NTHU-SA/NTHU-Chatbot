FROM python:3.13-slim

# 設定工作目錄
WORKDIR /usr/src/app

# 設定時區，並最小化生成額外檔案的指令
ENV TZ=Asia/Taipei \
    LOGURU_LEVEL=INFO \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080
RUN ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime && echo "$TZ" > /etc/timezone

# 複製並安裝依賴，使用 multi-stage 優化映像大小
COPY requirements.txt ./

# 使用 --no-cache-dir 與 --disable-pip-version-check 提高執行效率
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt

# 將專案程式碼複製到容器
COPY . .

# 暴露應用的默認埠
EXPOSE 8080

# 使用非 root 用戶執行程式以增強安全性
RUN useradd --create-home appuser
USER appuser

# 容器啟動時運行應用
CMD ["python", "main.py"]
