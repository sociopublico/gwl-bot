from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pipeline.config import load_session
from pipeline.gadebate import parse_speaker_page
from pipeline.roster import (
    chosen_source,
    merge_speakers,
    page_from_entry,
    speaker_entry_from_page,
    write_roster,
)


FIXTURE = Path(__file__).parent / "fixtures" / "gadebate_brazil.html"
LITHUANIA = Path(__file__).parent / "fixtures" / "gadebate_lithuania.html"


class RosterTest(unittest.TestCase):
    def test_brazil_fixture_has_pdf_other_audio_and_video(self) -> None:
        config = load_session("80")
        html = FIXTURE.read_text(encoding="utf-8")
        page = parse_speaker_page(
            config, "https://gadebate.un.org/en/80/brazil", html
        )
        entry = speaker_entry_from_page(page, config.sources)
        self.assertEqual(entry["slug"], "brazil")
        self.assertEqual(entry["country"], "Brazil")
        self.assertEqual(entry["speech_date"], "2025-09-23")
        self.assertEqual(entry["chosen"], "audio_en")
        self.assertIsNone(entry["sources"]["pdf_en"])
        self.assertEqual(entry["sources"]["pdf_other"]["filename"], "br_pt.pdf")
        self.assertEqual(entry["sources"]["audio_en"]["filename"], "80_BR_EN.mp3")
        self.assertEqual(entry["sources"]["video"]["entry_id"], "1_abc")
        restored = page_from_entry(entry)
        self.assertEqual(restored.audio_en.filename, "80_BR_EN.mp3")
        self.assertEqual(restored.pdf_other.filename, "br_pt.pdf")
        self.assertEqual(restored.video_entry_id, "1_abc")
        self.assertEqual(chosen_source(restored, config.sources), "audio_en")

    def test_lithuania_fixture_has_pdf_en_and_audio(self) -> None:
        config = load_session("80")
        html = LITHUANIA.read_text(encoding="utf-8")
        page = parse_speaker_page(
            config, "https://gadebate.un.org/en/80/lithuania", html
        )
        entry = speaker_entry_from_page(page, config.sources)
        self.assertEqual(entry["slug"], "lithuania")
        self.assertEqual(entry["chosen"], "pdf_en")
        self.assertEqual(entry["sources"]["pdf_en"]["filename"], "lt_en.pdf")
        self.assertEqual(entry["sources"]["audio_en"]["filename"], "80_LT_EN.mp3")
        self.assertIsNone(entry["sources"]["pdf_other"])

    def test_merge_speakers_replaces_by_slug(self) -> None:
        existing = {
            "session": 80,
            "day": "2025-09-23",
            "speakers": [
                {"slug": "brazil", "chosen": "audio_en"},
                {"slug": "kenya", "chosen": "pdf_en"},
            ],
        }
        incoming = {
            "session": 80,
            "day": "2025-09-23",
            "scraped_at": "now",
            "speakers": [{"slug": "lithuania", "chosen": "pdf_en"}],
        }
        merged = merge_speakers(existing, incoming)
        slugs = [s["slug"] for s in merged["speakers"]]
        self.assertEqual(slugs, ["brazil", "kenya", "lithuania"])
        replaced = merge_speakers(
            existing,
            {
                "session": 80,
                "day": "2025-09-23",
                "speakers": [{"slug": "brazil", "chosen": "pdf_other"}],
            },
        )
        self.assertEqual(replaced["speakers"][0]["chosen"], "pdf_other")
        self.assertEqual(len(replaced["speakers"]), 2)

    def test_write_roster_roundtrip(self) -> None:
        payload = {
            "session": 80,
            "day": "2025-09-23",
            "speakers": [{"slug": "brazil", "chosen": "audio_en"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = write_roster(payload, Path(tmp) / "2025-09-23.json")
            text = path.read_text(encoding="utf-8")
        self.assertIn('"brazil"', text)
        self.assertIn("audio_en", text)


if __name__ == "__main__":
    unittest.main()
