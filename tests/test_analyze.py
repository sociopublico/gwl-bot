from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.analyze import (
    ClaudeCallResult,
    UsageTotals,
    analysis_key,
    build_system_prompt,
    build_user_message,
    cache_control_from_env,
    cache_min_tokens,
    estimate_tokens,
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
        id_speech="M_12",
    )
    data.update(kwargs)
    return ExtractedSpeech(**data)


class AnalyzeHelpersTest(unittest.TestCase):
    def test_prompt_on_disk_is_not_stub(self) -> None:
        text = load_prompt(PIPELINE_ROOT / "data" / "analyze-prompt.md")
        self.assertFalse(prompt_is_stub(text))
        self.assertIn("summary", text)

    def test_prompt_is_stub_detects_markers(self) -> None:
        self.assertTrue(prompt_is_stub("# TODO: pegar el prompt de análisis\n"))
        self.assertTrue(prompt_is_stub("PEGAR_PROMPT here"))
        self.assertFalse(prompt_is_stub("summary — un párrafo en inglés"))

    def test_parse_json_object_strips_fences(self) -> None:
        raw = 'Here:\n```json\n{"summary": "hi", "notes": ""}\n```\n'
        self.assertEqual(parse_json_object(raw)["summary"], "hi")

    def test_parse_json_object_ignores_trailing_second_object(self) -> None:
        raw = '{"indicators": [{"id_speech": "M_1"}]}\n{"emerging_priorities": []}'
        parsed = parse_json_object(raw)
        self.assertEqual(parsed["indicators"][0]["id_speech"], "M_1")
        self.assertEqual(parsed["emerging_priorities"], [])

    def test_parse_json_object_allows_trailing_text(self) -> None:
        raw = '{"summary": "ok"}\nThanks!'
        self.assertEqual(parse_json_object(raw)["summary"], "ok")

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
        self.assertEqual(row["id_speech"], "M_12")
        self.assertEqual(row["ficha_url"], "https://gadebate.un.org/en/80/brazil")
        self.assertEqual(row["summary"], "Climate and multilateralism.")
        self.assertEqual(analysis_key(speech), "brazil|2025-09-23|M_12")
        self.assertIn("slug", identity_fields(speech))
        self.assertIn("id_speech", identity_fields(speech))

    def test_analysis_key_without_slug_uses_id(self) -> None:
        speech = _speech(slug="", country="", id_speech="M_99")
        self.assertEqual(analysis_key(speech), "|2025-09-23|M_99")
        other = _speech(slug="", country="", id_speech="M_100", name="Other")
        self.assertNotEqual(analysis_key(speech), analysis_key(other))

    def test_system_prompt_lists_columns_not_speech(self) -> None:
        system = build_system_prompt("Prompt body", columns=["slug", "summary"])
        self.assertIn("Prompt body", system)
        self.assertIn("summary", system)
        self.assertNotIn("Madam President", system)

    def test_user_message_is_speech_only(self) -> None:
        msg = build_user_message(_speech())
        self.assertIn("brazil", msg)
        self.assertIn("Madam President", msg)
        self.assertNotIn("Prompt body", msg)

    def test_cache_control_env(self) -> None:
        with patch.dict(os.environ, {"ANTHROPIC_CACHE_TTL": "1h"}, clear=False):
            self.assertEqual(
                cache_control_from_env(),
                {"type": "ephemeral", "ttl": "1h"},
            )
        with patch.dict(os.environ, {"ANTHROPIC_CACHE_TTL": "5m"}, clear=False):
            self.assertEqual(cache_control_from_env(), {"type": "ephemeral"})
        with patch.dict(os.environ, {"ANTHROPIC_CACHE_TTL": "off"}, clear=False):
            self.assertIsNone(cache_control_from_env())

    def test_cache_min_tokens_haiku(self) -> None:
        self.assertEqual(cache_min_tokens("claude-haiku-4-5"), 4096)
        self.assertGreaterEqual(estimate_tokens("abcd" * 1000), 1000)

    def test_usage_totals_cost(self) -> None:
        totals = UsageTotals()
        totals.add(
            {
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_creation_input_tokens": 4000,
                "cache_read_input_tokens": 0,
                "cache_creation": {"ephemeral_1h_input_tokens": 4000},
            }
        )
        totals.add(
            {
                "input_tokens": 2000,
                "output_tokens": 400,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 4000,
            }
        )
        self.assertEqual(totals.cache_hits, 1)
        self.assertEqual(totals.cache_writes, 1)
        # 1k*$1 + 0.9k*$5 + 4k*$2 + 4k*$0.10  (per MTok)
        expected = (
            1000 * 1.0
            + 2000 * 1.0
            + 900 * 5.0
            + 4000 * 2.0
            + 4000 * 0.10
        ) / 1_000_000
        self.assertAlmostEqual(totals.estimated_usd(), expected, places=9)

    def test_existing_analysis_keys(self) -> None:
        keys = existing_analysis_keys(
            [
                {
                    "slug": "brazil",
                    "date_time": "2025-09-23",
                    "id_speech": "M_12",
                    "ficha_url": "https://gadebate.un.org/en/80/brazil",
                }
            ]
        )
        self.assertIn("brazil|2025-09-23|M_12", keys)
        self.assertIn("M_12", keys)
        legacy = existing_analysis_keys(
            [{"slug": "brazil", "date_time": "2025-09-23", "ficha_url": ""}]
        )
        self.assertIn("brazil|2025-09-23", legacy)

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
        with (
            patch("pipeline.analyze.load_prompt", return_value="# TODO: pegar el prompt"),
            patch("pipeline.analyze.allow_stub", return_value=False),
        ):
            rc = analyze_day(config, day="2025-09-23", dry_run=False)
        self.assertEqual(rc, 1)

    def test_call_claude_posts_cached_system(self) -> None:
        from pipeline.analyze import call_claude

        payload = {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"summary": "ok", "notes": ""}),
                }
            ],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 0,
            },
        }
        with patch("pipeline.analyze._post_messages", return_value=payload) as posted:
            result = call_claude(
                "hello",
                api_key="sk-test",
                model="claude-haiku-4-5",
                system_prompt="SYSTEM PROMPT",
                cache_control={"type": "ephemeral", "ttl": "1h"},
            )
        self.assertIsInstance(result, ClaudeCallResult)
        self.assertEqual(result.data["summary"], "ok")
        self.assertEqual(result.usage["cache_creation_input_tokens"], 100)
        sent = posted.call_args[0][0]
        self.assertEqual(sent["model"], "claude-haiku-4-5")
        self.assertEqual(posted.call_args[0][1], "sk-test")
        self.assertIsInstance(sent["system"], list)
        self.assertEqual(sent["system"][0]["text"], "SYSTEM PROMPT")
        self.assertEqual(
            sent["system"][0]["cache_control"],
            {"type": "ephemeral", "ttl": "1h"},
        )
        self.assertEqual(sent["messages"][0]["content"], "hello")

    def test_call_claude_cache_off_uses_string_system(self) -> None:
        from pipeline.analyze import call_claude

        payload = {
            "content": [{"type": "text", "text": '{"summary": "x", "notes": ""}'}],
            "usage": {},
        }
        with patch("pipeline.analyze._post_messages", return_value=payload) as posted:
            call_claude(
                "u",
                api_key="sk",
                model="claude-haiku-4-5",
                system_prompt="S",
                cache_control=None,
            )
        sent = posted.call_args[0][0]
        self.assertEqual(sent["system"], "S")


if __name__ == "__main__":
    unittest.main()
