import json
from pathlib import Path

import structlog

from mappa.log import configure_logging, get_logger


def test_file_gets_json_lines_with_context_and_utc_time(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "mappa.jsonl"
    configure_logging(log_file)
    structlog.contextvars.bind_contextvars(snapshot_id="dev-01")
    try:
        get_logger("mappa.test").debug("fetch.ok", app_id="com.example.app", attempt=1)
    finally:
        structlog.contextvars.clear_contextvars()

    [line] = log_file.read_text().splitlines()
    event = json.loads(line)
    assert event["event"] == "fetch.ok"
    assert event["level"] == "debug"  # the file keeps everything, even below console level
    assert event["snapshot_id"] == "dev-01"
    assert event["app_id"] == "com.example.app"
    assert event["timestamp"].endswith("Z")


def test_tracebacks_never_include_local_variables(tmp_path: Path) -> None:
    log_file = tmp_path / "mappa.jsonl"
    configure_logging(log_file)
    api_key = "s3cret-androzoo-key"
    try:
        raise RuntimeError("download failed")
    except RuntimeError:
        get_logger("mappa.test").exception("apk.failed")

    text = log_file.read_text()
    assert "download failed" in text
    assert api_key not in text
