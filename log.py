import json
import logging
import os
import sys

from loguru import logger

# loguru 等級 → Cloud Logging severity
SEVERITY = {
    "TRACE": "DEBUG",
    "DEBUG": "DEBUG",
    "INFO": "INFO",
    "SUCCESS": "NOTICE",
    "WARNING": "WARNING",
    "ERROR": "ERROR",
    "CRITICAL": "CRITICAL",
}


def cloud_logging_line(record: dict) -> str:
    """
    把 loguru record 轉成 Cloud Logging 認得的單行 JSON。

    Cloud Run 會把 stdout 的 JSON 解析成 jsonPayload，`severity` 讓 Logging 分級與告警能正確運作；
    純文字輸出則全部變成 DEFAULT 等級。
    """
    message = record["message"]
    exception = record["exception"]
    # 只附上例外型別名稱：例外訊息與 traceback 可能夾帶 token 或使用者內容
    if exception is not None and exception.type is not None:
        message += f" [{exception.type.__name__}]"
    payload = {
        "severity": SEVERITY.get(record["level"].name, "DEFAULT"),
        "message": message,
        "logging.googleapis.com/sourceLocation": {
            "file": record["name"],
            "line": record["line"],
            "function": record["function"],
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def _cloud_sink(message) -> None:
    print(cloud_logging_line(message.record), file=sys.stdout, flush=True)


# K_SERVICE 只在 Cloud Run 上存在；本機維持 loguru 預設的彩色文字輸出
if os.getenv("K_SERVICE"):
    logger.remove()
    logger.add(_cloud_sink, level=os.getenv("LOGURU_LEVEL", "INFO"))

# Remove existing handlers
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)


class InterceptHandler(logging.Handler):
    def emit(self, record):
        # Get corresponding Loguru level
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller to get correct stack depth
        frame, depth = logging.currentframe(), 2
        while frame.f_back and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


# Intercept standard logging
logging.basicConfig(handlers=[InterceptHandler()], level=logging.INFO)

loggers = (
    "uvicorn",
    "uvicorn.access",
    "uvicorn.error",
    "fastapi",
    "asyncio",
    "starlette",
)

for logger_name in loggers:
    logging_logger = logging.getLogger(logger_name)
    logging_logger.handlers = []
    logging_logger.propagate = True

# 這些套件在 INFO 會記錄請求網址與內容；壓到 WARNING 避免洩漏 token 或對話內容。
for logger_name in ("httpx", "httpx2", "httpcore", "openai"):
    logging.getLogger(logger_name).setLevel(logging.WARNING)
