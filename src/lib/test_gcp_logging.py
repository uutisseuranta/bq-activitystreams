# src/lib/test_gcp_logging.py
#
# Yksikkötestit gcp_logging.py:lle.
# Ajetaan: pytest src/lib/test_gcp_logging.py
#
# Viite: #60 AC: yksikkötesti loggerin output-formaatille
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from lib.gcp_logging import StructuredFormatter, get_logger


class TestStructuredFormatter:
    def _format_record(self, msg: str, level: int = logging.INFO, exc_info=None) -> dict:
        record = logging.LogRecord(
            name="test-logger",
            level=level,
            pathname="",
            lineno=0,
            msg=msg,
            args=(),
            exc_info=exc_info,
        )
        formatter = StructuredFormatter()
        return json.loads(formatter.format(record))

    def test_severity_info(self):
        entry = self._format_record("hello", logging.INFO)
        assert entry["severity"] == "INFO"

    def test_severity_warning(self):
        entry = self._format_record("warn", logging.WARNING)
        assert entry["severity"] == "WARNING"

    def test_severity_error(self):
        entry = self._format_record("err", logging.ERROR)
        assert entry["severity"] == "ERROR"

    def test_message_field(self):
        entry = self._format_record("testviesti")
        assert entry["message"] == "testviesti"

    def test_logger_field(self):
        entry = self._format_record("x")
        assert entry["logger"] == "test-logger"

    def test_no_trace_without_env(self, monkeypatch):
        monkeypatch.delenv("CLOUD_TRACE_CONTEXT", raising=False)
        entry = self._format_record("x")
        assert "logging.googleapis.com/trace" not in entry

    def test_trace_with_env(self, monkeypatch):
        monkeypatch.setenv("CLOUD_TRACE_CONTEXT", "projects/test/traces/abc123")
        entry = self._format_record("x")
        assert entry["logging.googleapis.com/trace"] == "projects/test/traces/abc123"

    def test_output_is_valid_json(self):
        entry = self._format_record("json-testi")
        assert isinstance(entry, dict)

    def test_non_ascii_message(self):
        entry = self._format_record("ääkköset: äöå")
        assert "äöå" in entry["message"]


class TestGetLogger:
    def test_returns_logger(self):
        logger = get_logger("test-moduuli")
        assert isinstance(logger, logging.Logger)

    def test_idempotent(self):
        a = get_logger("idempotenssi-testi")
        b = get_logger("idempotenssi-testi")
        assert a is b
        assert len(a.handlers) == 1

    def test_logger_writes_json_to_stdout(self, capsys):
        logger = get_logger("stdout-testi-uniikki-123")
        logger.info("kirjoitetaan stdout:iin")
        captured = capsys.readouterr()
        entry = json.loads(captured.out.strip())
        assert entry["severity"] == "INFO"
        assert "kirjoitetaan" in entry["message"]
