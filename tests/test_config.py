from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.config import Config


class ConfigDefaultsTest(unittest.TestCase):
    def test_chunk_and_context_defaults(self) -> None:
        env = {"STREAM_URL": "https://www.youtube.com/watch?v=KnIFmbdRCi0"}
        with patch.dict(os.environ, env, clear=True):
            config = Config.from_env()
        self.assertEqual(config.chunk_seconds, 20)
        self.assertEqual(config.chunk_overlap_seconds, 4)
        self.assertEqual(config.context_words, 20)
        self.assertEqual(config.stream_start_seconds, 0)
        self.assertTrue(config.exit_on_eof)
        self.assertTrue(config.speaker_tracking)
        self.assertEqual(config.speaker_llm_model, "gpt-4o-mini")
        self.assertEqual(config.log_dir, "logs")
        self.assertEqual(config.smtp_password_b, "")
        self.assertFalse(config.smtp_rotation_enabled)
        self.assertEqual(config.watchdog_seconds, 180)
        self.assertEqual(config.watchdog_email_cooldown, 600)
        self.assertEqual(config.webtv_url, "")
        self.assertEqual(config.alert_text_before_seconds, 75)
        self.assertEqual(config.alert_text_after_seconds, 30)

    def test_webtv_url_from_env(self) -> None:
        env = {
            "STREAM_URL": "https://www.youtube.com/watch?v=KnIFmbdRCi0",
            "WEBTV_URL": "https://webtv.un.org/en/asset/k10/k10h1p03zp",
        }
        with patch.dict(os.environ, env, clear=True):
            config = Config.from_env()
        self.assertEqual(
            config.webtv_url,
            "https://webtv.un.org/en/asset/k10/k10h1p03zp",
        )

    def test_webtv_url_must_be_absolute(self) -> None:
        env = {
            "STREAM_URL": "https://www.youtube.com/watch?v=KnIFmbdRCi0",
            "WEBTV_URL": "not-a-url",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ValueError):
                Config.from_env()

    def test_stream_start_cannot_be_negative(self) -> None:
        env = {
            "STREAM_URL": "https://www.youtube.com/watch?v=KnIFmbdRCi0",
            "STREAM_START_SECONDS": "-1",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ValueError):
                Config.from_env()

    def test_alert_text_window_cannot_be_negative(self) -> None:
        env = {
            "STREAM_URL": "https://www.youtube.com/watch?v=KnIFmbdRCi0",
            "ALERT_TEXT_BEFORE_SECONDS": "-1",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ValueError):
                Config.from_env()


if __name__ == "__main__":
    unittest.main()
