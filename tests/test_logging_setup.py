"""Tests for the logging setup module."""

import logging
import time

import pytest

from core.logging_setup import (
    LOG_FILE_NAME,
    _resolve_level,
    log_activity,
    log_file_path,
    logs_dir,
    reset_logging,
    setup_logging,
)


@pytest.fixture(autouse=True)
def isolated_logging(tmp_path, monkeypatch):
    """Ensure every test starts with a clean logging state and isolated dir."""
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))
    reset_logging()
    yield
    reset_logging()


# ---------------------------------------------------------------------------
# _resolve_level
# ---------------------------------------------------------------------------

def test_resolve_level_standard():
    assert _resolve_level("DEBUG") == logging.DEBUG
    assert _resolve_level("INFO") == logging.INFO
    assert _resolve_level("WARNING") == logging.WARNING
    assert _resolve_level("ERROR") == logging.ERROR
    assert _resolve_level("CRITICAL") == logging.CRITICAL


def test_resolve_level_case_insensitive():
    assert _resolve_level("debug") == logging.DEBUG
    assert _resolve_level("Info") == logging.INFO
    assert _resolve_level("  WARNING  ") == logging.WARNING


def test_resolve_level_unknown_falls_back_to_info():
    assert _resolve_level("NONSENSE") == logging.INFO
    assert _resolve_level("") == logging.INFO
    assert _resolve_level(None) == logging.INFO  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# logs_dir and log_file_path
# ---------------------------------------------------------------------------

def test_logs_dir_created(tmp_path):
    d = logs_dir()
    assert d.exists()
    assert d.is_dir()
    assert d == tmp_path / "logs"


def test_log_file_path(tmp_path):
    path = log_file_path()
    assert path == tmp_path / "logs" / LOG_FILE_NAME


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------

def test_setup_logging_attaches_file_handler(tmp_path):
    setup_logging(level="DEBUG")
    root = logging.getLogger()
    # At least one file-backed handler should be present
    file_handlers = [
        h for h in root.handlers
        if isinstance(h, logging.handlers.TimedRotatingFileHandler)
    ]
    assert len(file_handlers) >= 1


def test_setup_logging_respects_explicit_level():
    setup_logging(level="WARNING")
    assert logging.getLogger().level == logging.WARNING


def test_setup_logging_is_idempotent():
    setup_logging(level="INFO")
    first_handlers = list(logging.getLogger().handlers)
    setup_logging(level="DEBUG")  # should be no-op
    second_handlers = list(logging.getLogger().handlers)
    assert first_handlers == second_handlers


def test_setup_logging_force_reconfigures():
    setup_logging(level="INFO")
    first_count = len(logging.getLogger().handlers)
    setup_logging(level="DEBUG", force=True)
    second_count = len(logging.getLogger().handlers)
    # Force detaches old, attaches new — count should be similar
    assert second_count >= 1
    assert logging.getLogger().level == logging.DEBUG
    # Unused assertion to avoid "first_count unused" warnings
    assert first_count >= 1


def test_setup_logging_writes_to_file(tmp_path):
    setup_logging(level="DEBUG")
    log = logging.getLogger("test_module")
    log.info("hello from test")
    # Flush handlers
    for h in logging.getLogger().handlers:
        h.flush()

    log_path = tmp_path / "logs" / LOG_FILE_NAME
    assert log_path.exists()
    content = log_path.read_text(encoding="utf-8")
    assert "hello from test" in content
    assert "test_module" in content
    assert "INFO" in content


def test_setup_logging_reads_from_settings_when_level_none(tmp_path, monkeypatch):
    # Write a settings.json with log_level=WARNING at the test user data dir
    import json
    settings_data = {"log_level": "WARNING"}
    (tmp_path / "settings.json").write_text(
        json.dumps(settings_data), encoding="utf-8"
    )
    setup_logging(level=None)
    assert logging.getLogger().level == logging.WARNING


# ---------------------------------------------------------------------------
# log_activity context manager
# ---------------------------------------------------------------------------

def test_log_activity_success_logs_start_and_completion(caplog):
    log = logging.getLogger("test_activity")
    log.setLevel(logging.DEBUG)
    with caplog.at_level(logging.DEBUG, logger="test_activity"):
        with log_activity(log, "doing work"):
            pass

    messages = [r.getMessage() for r in caplog.records]
    assert any("Starting doing work" in m for m in messages)
    assert any("doing work completed in" in m for m in messages)


def test_log_activity_exception_logs_error_and_reraises(caplog):
    log = logging.getLogger("test_activity_err")
    log.setLevel(logging.DEBUG)

    with caplog.at_level(logging.DEBUG, logger="test_activity_err"):
        with pytest.raises(ValueError, match="boom"):
            with log_activity(log, "risky op"):
                raise ValueError("boom")

    error_records = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(error_records) == 1
    assert "risky op failed after" in error_records[0].getMessage()
    assert "boom" in error_records[0].getMessage()
    # exc_info was captured
    assert error_records[0].exc_info is not None


def test_log_activity_timing_is_plausible(caplog):
    log = logging.getLogger("test_activity_timing")
    log.setLevel(logging.DEBUG)
    with caplog.at_level(logging.DEBUG, logger="test_activity_timing"):
        with log_activity(log, "slow op"):
            time.sleep(0.02)

    completion_messages = [
        r.getMessage() for r in caplog.records
        if "completed in" in r.getMessage()
    ]
    assert len(completion_messages) == 1
    # Message format: "slow op completed in 0.021s"
    msg = completion_messages[0]
    # Extract the timing value — should be >= 0.02
    import re
    match = re.search(r"completed in (\d+\.\d+)s", msg)
    assert match is not None
    elapsed = float(match.group(1))
    assert elapsed >= 0.02
    assert elapsed < 1.0  # sanity check


def test_log_activity_nested():
    log = logging.getLogger("test_nested")
    log.setLevel(logging.DEBUG)
    with log_activity(log, "outer"):
        with log_activity(log, "inner"):
            pass
    # No assertion — just verify nesting doesn't crash


# ---------------------------------------------------------------------------
# reset_logging
# ---------------------------------------------------------------------------

def test_reset_logging_removes_handlers():
    setup_logging(level="INFO")
    assert len(logging.getLogger().handlers) >= 1
    reset_logging()
    assert len(logging.getLogger().handlers) == 0


def test_reset_allows_reconfiguration():
    setup_logging(level="INFO")
    reset_logging()
    setup_logging(level="ERROR")
    assert logging.getLogger().level == logging.ERROR
