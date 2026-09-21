from __future__ import annotations

import logging
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import Config
from app.logger import HighlightsFilter
from app.notifier import EmailNotifier, NullNotifier, SmtpCredentials
from app.watchdog import (
    check_stale,
    heartbeat_is_fresh,
    heartbeat_path,
    write_heartbeat,
)


def _config(**overrides) -> Config:
    values = dict(
        stream_url="https://example.com/live",
        keywords=("women",),
        whisper_model="base",
        whisper_device="cpu",
        whisper_compute_type="int8",
        whisper_beam_size=1,
        whisper_vad=True,
        language="en",
        chunk_seconds=10,
        chunk_overlap_seconds=1,
        context_words=8,
        reconnect_delay=10,
        heartbeat_seconds=60,
        cpu_threads=4,
        log_level="INFO",
        log_dir="logs",
        cookies_file=None,
        audio_read_timeout=30,
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="user",
        smtp_password="secret",
        smtp_from="bot@example.com",
        smtp_user_b="",
        smtp_password_b="",
        smtp_from_b="",
        alert_email_to=("alerts@example.com",),
        smtp_starttls=True,
        smtp_ssl=False,
        alert_cooldown_seconds=120,
        smtp_timeout=15,
        watchdog_seconds=180,
        watchdog_email_cooldown=600,
    )
    values.update(overrides)
    return Config(**values)


class HeartbeatFileTest(unittest.TestCase):
    def test_write_and_freshness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_heartbeat(tmp)
            self.assertEqual(path, heartbeat_path(tmp))
            self.assertTrue(path.is_file())
            self.assertTrue(heartbeat_is_fresh(tmp, 180))
            old = time.time() - 400
            os.utime(path, (old, old))
            self.assertFalse(heartbeat_is_fresh(tmp, 180, now=time.time()))
            self.assertTrue(heartbeat_is_fresh(tmp, 0))

    def test_missing_file_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(heartbeat_is_fresh(tmp, 180))


class CheckStaleTest(unittest.TestCase):
    def test_noop_before_timeout(self) -> None:
        fired = check_stale(
            _config(),
            NullNotifier(),
            last_progress=100.0,
            now=200.0,
        )
        self.assertFalse(fired)

    def test_logs_when_stale(self) -> None:
        with self.assertLogs("app.watchdog", level="ERROR") as captured:
            fired = check_stale(
                _config(watchdog_seconds=180),
                NullNotifier(),
                last_progress=10.0,
                now=200.0,
                uptime=200.0,
                chunks=0,
            )
        self.assertTrue(fired)
        self.assertTrue(any("MONITOR_STALE" in line for line in captured.output))

    def test_disabled_when_zero(self) -> None:
        fired = check_stale(
            _config(watchdog_seconds=0),
            NullNotifier(),
            last_progress=0.0,
            now=10_000.0,
        )
        self.assertFalse(fired)


class StatusEmailTest(unittest.TestCase):
    def test_notify_status_respects_cooldown(self) -> None:
        sent: list[str] = []
        clock = {"now": 10.0}

        class Recording(EmailNotifier):
            def _send(self, subject: str, body: str, creds: SmtpCredentials) -> None:
                sent.append(subject)

        notifier = Recording(
            _config(watchdog_email_cooldown=600),
            clock=lambda: clock["now"],
        )
        self.assertTrue(notifier.notify_status("MONITOR_STALE | x", "body"))
        self.assertEqual(sent, ["MONITOR_STALE | x"])
        self.assertEqual(notifier.emails_sent, 1)

        clock["now"] = 100.0
        self.assertFalse(notifier.notify_status("MONITOR_STALE | x", "body"))
        self.assertEqual(len(sent), 1)

        clock["now"] = 700.0
        self.assertTrue(notifier.notify_status("MONITOR_STALE | x", "body"))
        self.assertEqual(len(sent), 2)

    def test_smtp_failure_does_not_arm_status_cooldown(self) -> None:
        clock = {"now": 10.0}

        class Failing(EmailNotifier):
            def _send(self, subject: str, body: str, creds: SmtpCredentials) -> None:
                raise OSError("smtp down")

        notifier = Failing(_config(), clock=lambda: clock["now"])
        self.assertFalse(notifier.notify_status("MONITOR_STALE | x", "body"))
        self.assertIsNone(notifier._last_status_sent)
        self.assertTrue(notifier.notify_status("MONITOR_STALE | x", "body") is False)

        clock["now"] = 11.0
        # still no cooldown; would send if SMTP worked
        self.assertIsNone(notifier._last_status_sent)

    def test_null_notifier_status_is_noop(self) -> None:
        self.assertFalse(NullNotifier().notify_status("MONITOR_STALE", "body"))


class HighlightsMonitorStaleTest(unittest.TestCase):
    def test_keeps_monitor_stale_at_info(self) -> None:
        filt = HighlightsFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="MONITOR_STALE | no transcript for 200s",
            args=(),
            exc_info=None,
        )
        self.assertTrue(filt.filter(record))


class ConfigWatchdogTest(unittest.TestCase):
    def test_defaults(self) -> None:
        env = {"STREAM_URL": "https://www.youtube.com/watch?v=KnIFmbdRCi0"}
        with patch.dict(os.environ, env, clear=True):
            config = Config.from_env()
        self.assertEqual(config.watchdog_seconds, 180)
        self.assertEqual(config.watchdog_email_cooldown, 600)

    def test_healthcheck_module_exit_codes(self) -> None:
        from app.healthcheck import main as healthcheck_main

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ,
                {"LOG_DIR": tmp, "WATCHDOG_SECONDS": "180"},
                clear=False,
            ):
                self.assertEqual(healthcheck_main(), 1)
                write_heartbeat(tmp)
                self.assertEqual(healthcheck_main(), 0)
            with patch.dict(
                os.environ,
                {"LOG_DIR": tmp, "WATCHDOG_SECONDS": "0"},
                clear=False,
            ):
                Path(heartbeat_path(tmp)).unlink(missing_ok=True)
                self.assertEqual(healthcheck_main(), 0)


if __name__ == "__main__":
    unittest.main()
