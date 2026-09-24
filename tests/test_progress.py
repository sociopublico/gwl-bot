from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.analyze import merge_analysis_row
from pipeline.config import load_session
from pipeline.models import ExtractedSpeech
from pipeline.progress import (
    build_day_progress,
    generate_progress_site,
    load_analysis_snapshot,
    write_analysis_snapshot,
    write_progress_site,
)
from pipeline.roster import write_roster
from pipeline.sheets import ANALYSIS_COLUMNS
from pipeline.store import out_dir, parse_speech_txt, write_speech


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
        via="whisper",
        elapsed_s=12.5,
    )
    data.update(kwargs)
    return ExtractedSpeech(**data)


def _roster_entry(**kwargs) -> dict:
    entry = {
        "slug": "brazil",
        "ficha_url": "https://gadebate.un.org/en/80/brazil",
        "country": "Brazil",
        "name": "Lula",
        "rank": "President",
        "speaker_title": "His Excellency",
        "speech_date": "2025-09-23",
        "chosen": "audio_en",
        "sources": {
            "pdf_en": None,
            "audio_en": {
                "label": "english",
                "url": "https://example/80_BR_EN.mp3",
                "filename": "80_BR_EN.mp3",
                "lang": "en",
            },
            "pdf_other": None,
            "audio_floor": None,
            "video": None,
        },
        "error": None,
        "http_status": 200,
    }
    entry.update(kwargs)
    return entry


class ProgressAggregatorTest(unittest.TestCase):
    def test_day_partial_then_complete_with_analysis(self) -> None:
        config = load_session("80")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Point config.root via a shallow replace of roster/analysis under a temp tree
            # by writing roster next to a forked config is hard; use real config paths
            # under a monkeypatched root instead.
            from dataclasses import replace

            cfg = replace(config, root=root)
            day = "2025-09-23"
            roster_dir = root / "data" / "roster" / "80"
            roster_dir.mkdir(parents=True)
            write_roster(
                {
                    "session": 80,
                    "day": day,
                    "scraped_at": "2026-09-18T12:00:00Z",
                    "speakers": [
                        _roster_entry(),
                        _roster_entry(
                            slug="kenya",
                            country="Kenya",
                            name="Ruto",
                            chosen="pdf_en",
                            sources={
                                "pdf_en": {
                                    "label": "en",
                                    "url": "https://example/ke.pdf",
                                    "filename": "ke.pdf",
                                    "lang": "en",
                                },
                                "audio_en": None,
                                "pdf_other": None,
                                "audio_floor": None,
                                "video": None,
                            },
                        ),
                    ],
                },
                roster_dir / f"{day}.json",
            )
            dest = root / "out"
            write_speech(_speech(), out_dir(cfg, day, dest=dest))

            day_prog = build_day_progress(cfg, day, dest=dest, repo_root=root)
            assert day_prog is not None
            self.assertEqual(day_prog["counts"]["speakers"], 2)
            self.assertEqual(day_prog["counts"]["speech_ok"], 1)
            self.assertEqual(day_prog["counts"]["analysis_ok"], 0)
            self.assertEqual(day_prog["status"], "en_progreso")

            brazil = next(s for s in day_prog["speakers"] if s["slug"] == "brazil")
            self.assertEqual(brazil["orador"]["status"], "ok")
            self.assertEqual(brazil["discurso"]["status"], "ok")
            self.assertEqual(brazil["discurso"]["via"], "whisper")
            self.assertEqual(brazil["discurso"]["elapsed_s"], 12.5)
            self.assertEqual(brazil["analisis"]["status"], "pending")
            kenya = next(s for s in day_prog["speakers"] if s["slug"] == "kenya")
            self.assertEqual(kenya["discurso"]["status"], "pending")

            row = merge_analysis_row(
                _speech(),
                {"summary": "Climate focus.", "notes": "Multilateralism."},
                columns=list(ANALYSIS_COLUMNS),
            )
            write_analysis_snapshot(
                cfg, day=day, slug="brazil", row=row, model="claude-haiku-4-5"
            )
            snap = load_analysis_snapshot(cfg, day, "brazil")
            assert snap is not None
            self.assertEqual(snap["summary"], "Climate focus.")

            day_prog2 = build_day_progress(cfg, day, dest=dest, repo_root=root)
            assert day_prog2 is not None
            brazil2 = next(s for s in day_prog2["speakers"] if s["slug"] == "brazil")
            self.assertEqual(brazil2["analisis"]["status"], "ok")
            self.assertEqual(brazil2["analisis"]["summary"], "Climate focus.")
            self.assertEqual(day_prog2["counts"]["analysis_ok"], 1)
            self.assertEqual(day_prog2["status"], "en_progreso")

    def test_day_with_roster_error(self) -> None:
        config = load_session("80")
        from dataclasses import replace

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = replace(config, root=root)
            day = "2025-09-24"
            roster_dir = root / "data" / "roster" / "80"
            roster_dir.mkdir(parents=True)
            write_roster(
                {
                    "session": 80,
                    "day": day,
                    "scraped_at": "now",
                    "speakers": [
                        _roster_entry(
                            slug="failsland",
                            country="Failsland",
                            name="",
                            chosen=None,
                            error="HTTP 404",
                            http_status=404,
                            sources={
                                "pdf_en": None,
                                "audio_en": None,
                                "pdf_other": None,
                                "audio_floor": None,
                                "video": None,
                            },
                        )
                    ],
                },
                roster_dir / f"{day}.json",
            )
            day_prog = build_day_progress(cfg, day, dest=root / "out", repo_root=root)
            assert day_prog is not None
            self.assertEqual(day_prog["status"], "con_errores")
            self.assertEqual(day_prog["speakers"][0]["orador"]["status"], "error")

    def test_via_persisted_in_txt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_speech(_speech(), Path(tmp))
            parsed = parse_speech_txt(path)
            self.assertEqual(parsed.via, "whisper")
            self.assertAlmostEqual(parsed.elapsed_s, 12.5)
            raw = path.read_text(encoding="utf-8")
            self.assertIn("via: whisper", raw)
            self.assertIn("elapsed_s:", raw)

    def test_generate_site_writes_html_and_json(self) -> None:
        config = load_session("80")
        from dataclasses import replace

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = replace(config, root=root)
            day = "2025-09-23"
            roster_dir = root / "data" / "roster" / "80"
            roster_dir.mkdir(parents=True)
            write_roster(
                {
                    "session": 80,
                    "day": day,
                    "scraped_at": "now",
                    "speakers": [_roster_entry()],
                },
                roster_dir / f"{day}.json",
            )
            docs = root / "docs"
            progress = generate_progress_site(
                cfg, docs_dir=docs, dest=root / "out", repo_root=root
            )
            self.assertEqual(len(progress["days"]), 1)
            self.assertTrue((docs / "80" / "index.html").is_file())
            payload = json.loads((docs / "80" / "data" / "progress.json").read_text())
            self.assertEqual(payload["session"], 80)
            self.assertEqual(payload["days"][0]["speakers"][0]["slug"], "brazil")
            html = (docs / "80" / "index.html").read_text(encoding="utf-8")
            self.assertIn("progress.json", html)
            self.assertIn("Fetch orador", html)
            self.assertIn("Sesión 81", html)
            home = (docs / "index.html").read_text(encoding="utf-8")
            self.assertIn("81/index.html", home)

    def test_write_progress_site_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp) / "docs"
            progress = {
                "session": 80,
                "name": "Test",
                "generated_at": "now",
                "days": [],
            }
            html_path, json_path = write_progress_site(progress, docs)
            self.assertTrue(html_path.is_file())
            self.assertEqual(json.loads(json_path.read_text())["name"], "Test")


class AnalysisSnapshotFromAnalyzeTest(unittest.TestCase):
    def test_analyze_writes_snapshot_on_success(self) -> None:
        from pipeline.analyze import analyze_day

        config = load_session("80")
        from dataclasses import replace

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = replace(config, root=root)
            day = "2025-09-23"
            dest = root / "out"
            write_speech(_speech(), out_dir(cfg, day, dest=dest))

            claude_payload = {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {"summary": "From Claude", "notes": "n1"}
                        ),
                    }
                ],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            }

            with (
                patch("pipeline.analyze.prompt_is_stub", return_value=False),
                patch("pipeline.analyze.load_prompt", return_value="real prompt"),
                patch("pipeline.analyze.can_write_sheets", return_value=True),
                patch(
                    "pipeline.sheets.read_analysis_records",
                    return_value=(list(ANALYSIS_COLUMNS), []),
                ),
                patch("pipeline.analyze._anthropic_settings", return_value=("sk", "m")),
                patch(
                    "pipeline.analyze._post_messages", return_value=claude_payload
                ),
                patch("pipeline.analyze.append_analysis_rows", return_value=[{"slug": "brazil"}]),
            ):
                rc = analyze_day(cfg, day=day, dest=dest)

            self.assertEqual(rc, 0)
            snap = load_analysis_snapshot(cfg, day, "brazil")
            assert snap is not None
            self.assertEqual(snap["summary"], "From Claude")
            self.assertEqual(snap["notes"], "n1")
            self.assertEqual(snap["model"], "m")


if __name__ == "__main__":
    unittest.main()
