from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.analyze import (
    analysis_key,
    build_user_message,
    identity_fields,
    load_prompt,
    merge_analysis_row,
    parse_json_object,
    prompt_is_stub,
)
from pipeline.config import PIPELINE_ROOT, load_session
from pipeline.models import ExtractedSpeech
from pipeline.sheets import ANALYSIS_COLUMNS, existing_analysis_keys
from pipeline.store import write_speech


def _speech(**kwargs) -> ExtractedSpeech:
    data = dict(
        session_id=80,
        slug="brazil",
        country="Brazil",
        name="Lula",
        rank="President",
        speech_date="2025-09-23",
        source="audio_en",
        source_url="https://example/80_BR_EN.mp3",
        language="en",
        text="Madam President, Brazil speaks today about climate.",
        speaker_title="His Excellency",
        original_language="pt",
        transformation="whisper",
    )
    data.update(kwargs)
    return ExtractedSpeech(**data)


class AnalyzeHelpersTest(unittest.TestCase):
    def test_stub_prompt_on_disk(self) -> None:
        text = load_prompt(PIPELINE_ROOT / "data" / "analyze-prompt.md")
        self.assertTrue(prompt_is_stub(text))

    def test_parse_json_object_strips_fences(self) -> None:
        raw = 'Here:\n```json\n{"summary": "hi", "notes": ""}\n```\n'
        self.assertEqual(parse_json_object(raw)["summary"], "hi")

    def test_merge_overwrites_identity(self) -> None:
        speech = _speech()
        row = merge_analysis_row(
            speech,
            {
                "slug": "forged",
                "summary": "Climate and multilateralism.",
                "notes": "",
            },
            columns=list(ANALYSIS_COLUMNS),
        )
        self.assertEqual(row["slug"], "brazil")
        self.assertEqual(row["date_time"], "2025-09-23")
        self.assertEqual(row["ficha_url"], "https://gadebate.un.org/en/80/brazil")
        self.assertEqual(row["summary"], "Climate and multilateralism.")
        self.assertEqual(analysis_key(speech), "brazil|2025-09-23")
        self.assertIn("slug", identity_fields(speech))

    def test_user_message_lists_columns(self) -> None:
        msg = build_user_message("Prompt body", _speech(), columns=["slug", "summary"])
        self.assertIn("Prompt body", msg)
        self.assertIn("summary", msg)
        self.assertIn("brazil", msg)

    def test_existing_analysis_keys(self) -> None:
        keys = existing_analysis_keys(
            [
                {
                    "slug": "brazil",
                    "date_time": "2025-09-23",
                    "ficha_url": "https://gadebate.un.org/en/80/brazil",
                }
            ]
        )
        self.assertIn("brazil|2025-09-23", keys)

    def test_dry_run_lists_speeches_without_api(self) -> None:
        from pipeline.analyze import analyze_day
        from pipeline.store import out_dir

        config = load_session("80")
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            directory = out_dir(config, "2025-09-23", dest=dest)
            write_speech(_speech(), directory)
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False):
                rc = analyze_day(
                    config,
                    day="2025-09-23",
                    dest=dest,
                    dry_run=True,
                )
        self.assertEqual(rc, 0)

    def test_stub_without_allow_exits(self) -> None:
        from pipeline.analyze import analyze_day

        config = load_session("80")
        with patch("pipeline.analyze.allow_stub", return_value=False):
            rc = analyze_day(config, day="2025-09-23", dry_run=False)
        self.assertEqual(rc, 1)

    def test_call_claude_posts_messages(self) -> None:
        from pipeline.analyze import call_claude

        payload = {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"summary": "ok", "notes": ""}),
                }
            ]
        }
        with patch("pipeline.analyze._post_messages", return_value=payload) as posted:
            row = call_claude("hello", api_key="sk-test", model="claude-haiku-4-5")
        self.assertEqual(row["summary"], "ok")
        sent = posted.call_args[0][0]
        self.assertEqual(sent["model"], "claude-haiku-4-5")
        self.assertEqual(posted.call_args[0][1], "sk-test")


if __name__ == "__main__":
    unittest.main()
