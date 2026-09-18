import os

import uvicorn
from dotenv import load_dotenv

from log import logger

load_dotenv()

if __name__ == "__main__":
    if os.getenv("DEV_MODE") == "True":
        logger.info("🚀 Running in Development mode")
        os.environ["LOGURU_LEVEL"] = "DEBUG"
        uvicorn.run(
            "src.app:create_app",
            factory=True,
            host="0.0.0.0",
            port=int(os.getenv("PORT") or 5000),
            log_config=None,
            log_level=None,
            reload=True,  # reload the server every time code changes
            timeout_keep_alive=75,  # LIFF 對話使用 SSE 長連線
            proxy_headers=True,
            forwarded_allow_ips="*",
        )
    else:
        logger.info("🚀 Running in Production mode")
        os.environ["LOGURU_LEVEL"] = "ERROR"
        uvicorn.run(
            "src.app:create_app",
            factory=True,
            host="0.0.0.0",
            port=int(os.getenv("PORT") or 5000),
            log_config=None,
            log_level=None,
            timeout_keep_alive=75,  # LIFF 對話使用 SSE 長連線
            proxy_headers=True,  # Cloud Run 前端代理的 X-Forwarded-* 標頭
            forwarded_allow_ips="*",
        )
