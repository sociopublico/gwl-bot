from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.logger import (
    KEYWORD_DETECTED,
    SPEAKER_CHANGED,
    FlushingStreamHandler,
    FlushingTimedRotatingFileHandler,
    HighlightsFilter,
    setup_logging,
)


class HighlightsFilterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.filter = HighlightsFilter()

    def _record(self, level: int, message: str) -> logging.LogRecord:
        return logging.LogRecord(
            name="test",
            level=level,
            pathname=__file__,
            lineno=1,
            msg=message,
            args=(),
            exc_info=None,
        )

    def test_keeps_keyword_and_speaker_levels(self) -> None:
        self.assertTrue(self.filter.filter(self._record(KEYWORD_DETECTED, "women | …")))
        self.assertTrue(self.filter.filter(self._record(SPEAKER_CHANGED, "0:00 | Lula | …")))

    def test_keeps_email_and_session_info(self) -> None:
        self.assertTrue(
            self.filter.filter(self._record(logging.INFO, "Email alert sent | to=a@b.c"))
        )
        self.assertTrue(
            self.filter.filter(
                self._record(logging.INFO, "EMAIL_SKIPPED_COOLDOWN | keywords=women")
            )
        )
        self.assertTrue(
            self.filter.filter(
                self._record(logging.INFO, "EMAIL_WAITING_CONTEXT | keywords=women")
            )
        )
        self.assertTrue(
            self.filter.filter(self._record(logging.INFO, "MONITOR_STALE | no transcript"))
        )

    def test_drops_routine_transcription(self) -> None:
        self.assertFalse(
            self.filter.filter(
                self._record(logging.INFO, "Transcribed chunk | audio=20.0s | text=hello")
            )
        )
        self.assertFalse(
            self.filter.filter(
                self._record(logging.INFO, "Still listening | uptime=60s | chunks=3")
            )
        )

    def test_keeps_warnings_and_errors(self) -> None:
        self.assertTrue(self.filter.filter(self._record(logging.WARNING, "Reconnecting in 10s")))
        self.assertTrue(self.filter.filter(self._record(logging.ERROR, "Stream error: boom")))


class SetupLoggingTest(unittest.TestCase):
    def tearDown(self) -> None:
        root = logging.getLogger()
        for handler in list(root.handlers):
            handler.close()
            root.removeHandler(handler)

    def test_writes_full_and_highlights_with_flush(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            setup_logging("INFO", tmp)
            logger = logging.getLogger("app.test_logging")
            logger.info("Transcribed chunk | audio=1.0s | text=hello")
            logger.log(KEYWORD_DETECTED, "women | unknown | \"...women...\"")

            full = Path(tmp) / "full.log"
            highlights = Path(tmp) / "highlights.log"
            self.assertTrue(full.is_file())
            self.assertTrue(highlights.is_file())
            full_text = full.read_text(encoding="utf-8")
            hi_text = highlights.read_text(encoding="utf-8")
            self.assertIn("Transcribed chunk", full_text)
            self.assertIn("KEYWORD_DETECTED", full_text)
            self.assertIn("KEYWORD_DETECTED", hi_text)
            self.assertNotIn("Transcribed chunk", hi_text)

            root = logging.getLogger()
            self.assertTrue(any(isinstance(h, FlushingStreamHandler) for h in root.handlers))
            self.assertTrue(
                any(isinstance(h, FlushingTimedRotatingFileHandler) for h in root.handlers)
            )

    def test_flushing_handler_calls_flush(self) -> None:
        stream = __import__("io").StringIO()
        handler = FlushingStreamHandler(stream)
        with patch.object(handler, "flush") as flush:
            record = logging.LogRecord(
                name="t",
                level=logging.INFO,
                pathname=__file__,
                lineno=1,
                msg="hello",
                args=(),
                exc_info=None,
            )
            handler.emit(record)
            flush.assert_called()


if __name__ == "__main__":
    unittest.main()
