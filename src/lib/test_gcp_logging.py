# src/lib/test_gcp_logging.py
import json
import logging
import os
import sys
import io
import unittest
from unittest.mock import patch

from lib.gcp_logging import StructuredFormatter, get_logger


class TestStructuredFormatter(unittest.TestCase):
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
        self.assertEqual(entry["severity"], "INFO")

    def test_severity_warning(self):
        entry = self._format_record("warn", logging.WARNING)
        self.assertEqual(entry["severity"], "WARNING")

    def test_severity_error(self):
        entry = self._format_record("err", logging.ERROR)
        self.assertEqual(entry["severity"], "ERROR")

    def test_message_field(self):
        entry = self._format_record("testviesti")
        self.assertEqual(entry["message"], "testviesti")

    def test_logger_field(self):
        entry = self._format_record("x")
        self.assertEqual(entry["logger"], "test-logger")

    def test_no_trace_without_env(self):
        with patch.dict(os.environ, {}, clear=True):
            entry = self._format_record("x")
            self.assertNotIn("logging.googleapis.com/trace", entry)

    def test_trace_with_env(self):
        with patch.dict(os.environ, {"CLOUD_TRACE_CONTEXT": "projects/test/traces/abc123"}):
            entry = self._format_record("x")
            self.assertEqual(entry["logging.googleapis.com/trace"], "projects/test/traces/abc123")

    def test_output_is_valid_json(self):
        entry = self._format_record("json-testi")
        self.assertTrue(isinstance(entry, dict))

    def test_non_ascii_message(self):
        entry = self._format_record("ääkköset: äöå")
        self.assertIn("äöå", entry["message"])


class TestGetLogger(unittest.TestCase):
    def test_returns_logger(self):
        logger = get_logger("test-moduuli")
        self.assertTrue(isinstance(logger, logging.Logger))

    def test_idempotent(self):
        a = get_logger("idempotenssi-testi")
        b = get_logger("idempotenssi-testi")
        self.assertIs(a, b)
        self.assertEqual(len(a.handlers), 1)

    def test_logger_writes_json_to_stdout(self):
        logger = get_logger("stdout-testi-uniikki-123")
        f = io.StringIO()
        # Korvataan loggerin stream tilapäisesti f:llä
        logger.handlers[0].stream = f
        try:
            logger.info("kirjoitetaan stdout:iin")
            output = f.getvalue().strip()
            entry = json.loads(output)
            self.assertEqual(entry["severity"], "INFO")
            self.assertIn("kirjoitetaan", entry["message"])
        finally:
            logger.handlers[0].stream = sys.stdout
