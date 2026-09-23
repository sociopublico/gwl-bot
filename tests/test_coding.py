from __future__ import annotations

import csv
import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from pipeline.coding import (
    CHUNK_SIZE,
    EMERGING_COLUMNS,
    INDICATOR_COLUMNS,
    INDICATORS,
    chunk_speeches,
    code_day,
    consistency_warnings,
    emerging_extract_id,
    extract_id_for,
    flatten_newlines,
    incomplete_speeches,
    option_for,
    rows_from_payload,
    topic_slug,
)
from pipeline.config import load_session
from pipeline.models import ExtractedSpeech
from pipeline.store import out_dir, write_speech


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
        text="Madam President, Brazil speaks today about climate and UN reform.",
        speaker_title="His Excellency",
        original_language="pt",
        transformation="whisper",
        id_speech="M_1",
    )
    data.update(kwargs)
    return ExtractedSpeech(**data)


def _prompt(directory: Path) -> Path:
    path = directory / "prompt.md"
    path.write_text("UNGA speech coding methodology.\n", encoding="utf-8")
    return path


def _payload(
    speech_id: str,
    *,
    codes: dict[str, int] | None = None,
    extract: str = "(1) Full sentence about the topic.",
    topics: list[str] | None = None,
) -> dict:
    codes = codes or {}
    indicators = []
    for name in INDICATORS:
        default = 5 if name == "future_multilateralism_position" else 1
        if name == "un_reform_position":
            default = 0
        code = codes.get(name, default)
        indicators.append(
            {
                "id_speech": speech_id,
                "indicator_name": name,
                "code": code,
                "textual_extract": extract if code else "No mention",
                "coder_notes": "",
            }
        )
    emerging = [
        {
            "id_speech": speech_id,
            "emerging_topic": topic,
            "textual_extract": "One line on the theme.",
        }
        for topic in (topics if topics is not None else ["Climate change"])
    ]
    return {"indicators": indicators, "emerging_priorities": emerging}


class CodebookHelpersTest(unittest.TestCase):
    def test_flatten_newlines(self) -> None:
        self.assertEqual(flatten_newlines("a\nb\nc"), "a | b | c")

    def test_future_option_nonsequential_codes(self) -> None:
        self.assertEqual(
            option_for("future_multilateralism_position", 5),
            "Preservation / Strengthening",
        )
        self.assertEqual(option_for("future_multilateralism_position", 6), "Transformation")
        self.assertEqual(option_for("un_reform_position", 1), "Positive / Supportive")

    def test_extract_ids(self) -> None:
        self.assertEqual(
            extract_id_for("M_3", "un_reform_position"),
            "M_3_un_reform_position",
        )
        self.assertEqual(topic_slug("Climate change"), "climate_change")
        self.assertEqual(
            emerging_extract_id("M_3", "Climate change"),
            "M_3_climate_change",
        )
        self.assertEqual(
            emerging_extract_id("M_3", "AI / digital transformation"),
            "M_3_ai_digital_transformation",
        )

    def test_chunk_size_three(self) -> None:
        items = [(_speech(id_speech=f"M_{n}"), Path(f"M_{n}.txt"), f"M_{n}") for n in range(1, 5)]
        chunks = chunk_speeches(items)
        self.assertEqual(CHUNK_SIZE, 3)
        self.assertEqual(len(chunks), 2)
        self.assertEqual([item[2] for item in chunks[0]], ["M_1", "M_2", "M_3"])
        self.assertEqual([item[2] for item in chunks[1]], ["M_4"])

    def test_consistency_warning_on_nested_zero(self) -> None:
        rows = [
            {"id_speech": "M_1", "indicator_name": "sg_selection_position_gender", "code": "1"},
            {"id_speech": "M_1", "indicator_name": "sg_selection_position", "code": "0"},
            {"id_speech": "M_1", "indicator_name": "gender_equality_position", "code": "0"},
        ]
        warnings = consistency_warnings(rows)
        self.assertTrue(any("sg_selection_position=0" in item for item in warnings))
        self.assertTrue(any("gender_equality_position=0" in item for item in warnings))

    def test_un_reform_positive_aligns_future_to_transformation(self) -> None:
        payload = _payload(
            "M_1",
            codes={
                "un_reform_position": 1,
                "future_multilateralism_position": 5,
            },
            topics=[],
        )
        indicators, _ = rows_from_payload(
            payload,
            [(_speech(), Path("M_1.txt"), "M_1")],
            model="claude-haiku-4-5",
            date_coded="2026-09-15",
        )
        future = next(
            row for row in indicators if row["indicator_name"] == "future_multilateralism_position"
        )
        self.assertEqual(future["code"], "6")
        self.assertEqual(future["option"], "Transformation")
        self.assertIn("un_reform Positive", future["coder_notes"])

    def test_un_reform_positive_warns_if_future_not_transformation(self) -> None:
        rows = [
            {"id_speech": "M_1", "indicator_name": "un_reform_position", "code": "1"},
            {"id_speech": "M_1", "indicator_name": "future_multilateralism_position", "code": "5"},
        ]
        warnings = consistency_warnings(rows)
        self.assertTrue(any("future_multilateralism_position=5" in item for item in warnings))

    def test_methodology_covers_pga_gender_and_restore_rules(self) -> None:
        from pipeline.coding import DEFAULT_PROMPT_PATH, API_INSTRUCTIONS

        text = DEFAULT_PROMPT_PATH.read_text(encoding="utf-8")
        self.assertIn("Annalena Baerbock", text)
        self.assertIn("President of the General Assembly", text)
        self.assertIn("gender imbalance", text)
        self.assertIn("Women, Peace and Security", text)
        self.assertIn("restore credibility", text)
        self.assertIn("UN-80", text)
        self.assertIn("women and children", text.casefold())
        self.assertIn("Annalena Baerbock", API_INSTRUCTIONS)
        self.assertIn("UN80", API_INSTRUCTIONS)

    def test_rows_from_payload_fills_eight_and_skips_unknown_topic(self) -> None:
        speech = _speech()
        path = Path("M_1.txt")
        payload = _payload("M_1", topics=["Climate change", "Not a real theme"])
        payload["indicators"].append(
            {
                "id_speech": "M_99",
                "indicator_name": "un_reform_position",
                "code": 3,
                "textual_extract": "forged",
                "coder_notes": "",
            }
        )
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            indicators, emerging = rows_from_payload(
                payload,
                [(speech, path, "M_1")],
                model="claude-haiku-4-5",
                date_coded="2026-09-15",
            )
        self.assertEqual(len(indicators), 8)
        by_name = {row["indicator_name"]: row for row in indicators}
        self.assertEqual(by_name["future_multilateralism_position"]["option"], "Preservation / Strengthening")
        self.assertEqual(by_name["un_reform_position"]["extract_id"], "M_1_un_reform_position")
        self.assertEqual(by_name["un_reform_position"]["coding_source"], "claude")
        self.assertEqual(by_name["un_reform_position"]["model"], "claude-haiku-4-5")
        self.assertEqual(len(emerging), 1)
        self.assertEqual(emerging[0]["id_extract"], "M_1_climate_change")
        self.assertIn("Not a real theme", stderr.getvalue())
        self.assertFalse(any(row["id_speech"] == "M_99" for row in indicators))

    def test_missing_indicator_is_incomplete_not_zero(self) -> None:
        payload = {
            "indicators": [
                {
                    "id_speech": "M_1",
                    "indicator_name": "un_reform_position",
                    "code": 1,
                    "textual_extract": "(1) Reform now.",
                    "coder_notes": "",
                }
            ],
            "emerging_priorities": [],
        }
        items = [(_speech(), Path("M_1.txt"), "M_1")]
        missing = incomplete_speeches(payload, items)
        self.assertIn("M_1", missing)
        self.assertIn("gender_equality_position", missing["M_1"])
        indicators, emerging = rows_from_payload(
            payload,
            items,
            model="claude-haiku-4-5",
            date_coded="2026-09-15",
        )
        self.assertEqual(indicators, [])
        self.assertEqual(emerging, [])

    def test_invalid_code_is_incomplete(self) -> None:
        payload = _payload("M_1", codes={"gender_equality_position": 99}, topics=[])
        missing = incomplete_speeches(
            payload, [(_speech(), Path("M_1.txt"), "M_1")]
        )
        self.assertIn("gender_equality_position", missing["M_1"])

    def test_code_zero_drops_textual_extract(self) -> None:
        payload = _payload(
            "M_1",
            codes={"gender_equality_position": 0, "un_reform_position": 1},
            topics=[],
        )
        indicators, _ = rows_from_payload(
            payload,
            [(_speech(), Path("M_1.txt"), "M_1")],
            model="claude-haiku-4-5",
            date_coded="2026-09-15",
        )
        gender = next(
            row for row in indicators if row["indicator_name"] == "gender_equality_position"
        )
        reform = next(
            row for row in indicators if row["indicator_name"] == "un_reform_position"
        )
        self.assertEqual(gender["code"], "0")
        self.assertEqual(gender["textual_extract"], "")
        self.assertTrue(reform["textual_extract"])


class CodeDayTest(unittest.TestCase):
    def test_dry_run_lists_chunks_without_api(self) -> None:
        config = load_session("80")
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            prompt = _prompt(dest)
            directory = out_dir(config, "2025-09-23", dest=dest)
            write_speech(_speech(), directory)
            write_speech(_speech(slug="chile", id_speech="M_2", name="Boric"), directory)
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False):
                    rc = code_day(
                        config,
                        day="2025-09-23",
                        dest=dest,
                        prompt_path=prompt,
                        dry_run=True,
                    )
        self.assertEqual(rc, 0)
        log = stderr.getvalue()
        self.assertIn("DRY chunk 1/1", log)
        self.assertIn("M_1", log)
        self.assertIn("M_2", log)

    def test_writes_two_csvs_and_flattens_newlines(self) -> None:
        config = load_session("80")
        payload = _payload(
            "M_1",
            extract="(1) First line.\n(2) Second line.",
            topics=["Climate change"],
        )

        def fake_claude(_message, **_kwargs):
            return payload

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            prompt = _prompt(dest)
            directory = out_dir(config, "2025-09-23", dest=dest)
            write_speech(_speech(), directory)
            with patch("pipeline.coding.anthropic_settings", return_value=("sk", "claude-haiku-4-5")):
                with patch("pipeline.coding.call_claude", side_effect=fake_claude) as mocked:
                    rc = code_day(
                        config,
                        day="2025-09-23",
                        dest=dest,
                        prompt_path=prompt,
                    )
            self.assertEqual(rc, 0)
            self.assertEqual(mocked.call_args.kwargs["max_tokens"], 32768)
            self.assertTrue(mocked.call_args.kwargs.get("disable_thinking"))
            self.assertTrue(mocked.call_args.kwargs["cache_system"])
            self.assertIn("methodology", mocked.call_args.kwargs["system"])

            indicators_path = directory / "Indicators.csv"
            emerging_path = directory / "Emerging_Priorities.csv"
            self.assertTrue(indicators_path.is_file())
            self.assertTrue(emerging_path.is_file())
            with indicators_path.open(encoding="utf-8", newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(list(rows[0].keys()), INDICATOR_COLUMNS)
            self.assertEqual(len(rows), 8)
            gender = next(
                row for row in rows if row["indicator_name"] == "gender_equality_position"
            )
            self.assertEqual(gender["extract_id"], "M_1_gender_equality_position")
            self.assertIn(" | ", gender["textual_extract"])
            self.assertNotIn("\n", gender["textual_extract"])
            future = next(
                row for row in rows if row["indicator_name"] == "future_multilateralism_position"
            )
            self.assertEqual(future["code"], "5")
            self.assertEqual(future["option"], "Preservation / Strengthening")
            with emerging_path.open(encoding="utf-8", newline="") as fh:
                emerging = list(csv.DictReader(fh))
            self.assertEqual(list(emerging[0].keys()), EMERGING_COLUMNS)
            self.assertEqual(emerging[0]["id_extract"], "M_1_climate_change")

    def test_skips_id_already_in_csv_unless_force(self) -> None:
        config = load_session("80")
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            prompt = _prompt(dest)
            directory = out_dir(config, "2025-09-23", dest=dest)
            write_speech(_speech(), directory)
            write_speech(_speech(slug="chile", id_speech="M_2", name="Boric"), directory)
            existing = directory / "Indicators.csv"
            with existing.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=INDICATOR_COLUMNS)
                writer.writeheader()
                writer.writerow(
                    {col: "" for col in INDICATOR_COLUMNS}
                    | {"id_speech": "M_1", "indicator_name": "un_reform_position"}
                )

            calls: list[str] = []

            def fake_claude(message, **_kwargs):
                calls.append(message)
                return _payload("M_2", topics=[])

            with patch("pipeline.coding.anthropic_settings", return_value=("sk", "claude-haiku-4-5")):
                with patch("pipeline.coding.call_claude", side_effect=fake_claude):
                    rc = code_day(
                        config,
                        day="2025-09-23",
                        dest=dest,
                        prompt_path=prompt,
                    )
            self.assertEqual(rc, 0)
            self.assertEqual(len(calls), 1)
            self.assertIn("id_speech=M_2", calls[0])
            self.assertNotIn("id_speech=M_1", calls[0])

            with patch("pipeline.coding.anthropic_settings", return_value=("sk", "claude-haiku-4-5")):
                with patch(
                    "pipeline.coding.call_claude",
                    return_value=_payload("M_1", topics=[]),
                ) as forced:
                    rc = code_day(
                        config,
                        day="2025-09-23",
                        dest=dest,
                        prompt_path=prompt,
                        force=True,
                        slug="brazil",
                    )
            self.assertEqual(rc, 0)
            self.assertEqual(forced.call_count, 1)
            with existing.open(encoding="utf-8", newline="") as fh:
                after = list(csv.DictReader(fh))
            self.assertEqual(sum(1 for row in after if row["id_speech"] == "M_1"), 8)
            self.assertEqual(sum(1 for row in after if row["id_speech"] == "M_2"), 8)

    def test_skips_speech_without_id(self) -> None:
        config = load_session("80")
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            prompt = _prompt(dest)
            directory = out_dir(config, "2025-09-23", dest=dest)
            write_speech(_speech(id_speech=""), directory)
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                rc = code_day(
                    config,
                    day="2025-09-23",
                    dest=dest,
                    prompt_path=prompt,
                    dry_run=True,
                )
            self.assertEqual(rc, 1)
            self.assertIn("sin id_speech", stderr.getvalue())

    def test_cli_dry_run(self) -> None:
        from pipeline.cli import main

        config = load_session("80")
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            prompt = _prompt(dest)
            directory = out_dir(config, "2025-09-23", dest=dest)
            write_speech(_speech(), directory)
            rc = main(
                [
                    "coding",
                    "--session",
                    "80",
                    "--day",
                    "2025-09-23",
                    "--out",
                    str(dest),
                    "--prompt",
                    str(prompt),
                    "--dry-run",
                ]
            )
            self.assertEqual(rc, 0)


    def test_mixed_chunk_writes_only_complete_speech(self) -> None:
        config = load_session("80")
        complete = _payload("M_1", topics=["Climate change"])
        incomplete = {
            "indicators": [
                {
                    "id_speech": "M_2",
                    "indicator_name": "un_reform_position",
                    "code": 1,
                    "textual_extract": "(1) Reform.",
                    "coder_notes": "",
                }
            ],
            "emerging_priorities": [
                {
                    "id_speech": "M_2",
                    "emerging_topic": "Climate change",
                    "textual_extract": "should not be written",
                }
            ],
        }
        payload = {
            "indicators": complete["indicators"] + incomplete["indicators"],
            "emerging_priorities": complete["emerging_priorities"]
            + incomplete["emerging_priorities"],
        }

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            prompt = _prompt(dest)
            directory = out_dir(config, "2025-09-23", dest=dest)
            write_speech(_speech(), directory)
            write_speech(_speech(slug="chile", id_speech="M_2", name="Boric"), directory)
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                with patch(
                    "pipeline.coding.anthropic_settings",
                    return_value=("sk", "claude-haiku-4-5"),
                ):
                    with patch("pipeline.coding.call_claude", return_value=payload):
                        rc = code_day(
                            config,
                            day="2025-09-23",
                            dest=dest,
                            prompt_path=prompt,
                        )
            self.assertEqual(rc, 2)
            self.assertIn("FAIL speech=M_2", stderr.getvalue())
            with (directory / "Indicators.csv").open(encoding="utf-8", newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual({row["id_speech"] for row in rows}, {"M_1"})
            self.assertEqual(len(rows), 8)
            with (directory / "Emerging_Priorities.csv").open(
                encoding="utf-8", newline=""
            ) as fh:
                emerging = list(csv.DictReader(fh))
            self.assertEqual([row["id_speech"] for row in emerging], ["M_1"])


if __name__ == "__main__":
    unittest.main()
