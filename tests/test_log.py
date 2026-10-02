import json

from loguru import logger

from log import cloud_logging_line


def capture(emit) -> dict:
    """用暫時的 sink 抓下一筆 record，轉成 Cloud Logging JSON 後解析。"""
    lines: list[str] = []
    sink_id = logger.add(lambda message: lines.append(cloud_logging_line(message.record)))
    try:
        emit()
    finally:
        logger.remove(sink_id)
    return json.loads(lines[-1])


def test_levels_map_to_cloud_logging_severity():
    assert capture(lambda: logger.info("啟動"))["severity"] == "INFO"
    assert capture(lambda: logger.warning("LLM API error: 401"))["severity"] == "WARNING"
    entry = capture(lambda: logger.error("Chat stream failed: {}", "TimeoutError"))
    assert entry["severity"] == "ERROR"
    assert entry["message"] == "Chat stream failed: TimeoutError"
    assert entry["logging.googleapis.com/sourceLocation"]["file"].endswith("test_log")


def test_exception_only_adds_type_name_not_message_or_traceback():
    def emit():
        try:
            raise RuntimeError("Bearer secret-token")
        except RuntimeError:
            logger.opt(exception=True).error("verify failed")

    entry = capture(emit)
    assert entry["message"] == "verify failed [RuntimeError]"
    assert "secret-token" not in json.dumps(entry)
    assert "Traceback" not in entry["message"]
