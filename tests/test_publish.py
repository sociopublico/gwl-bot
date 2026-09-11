from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.config import load_session, load_slugs
from pipeline.countries import NO_ISO_SLUGS, load_countries_file
from pipeline.extract_video import kaltura_play_url
from pipeline.metadata import (
    build_metadata_row,
    infer_speaker_level,
    language_label,
    next_speech_id,
)
from pipeline.models import ExtractedSpeech
from pipeline.publish import _parse_github_remote, transcript_url_for, write_metadata_csv
from pipeline.sheets import existing_keys
from pipeline.store import parse_speech_txt, speech_to_txt


class CountryJoinTest(unittest.TestCase):
    def test_aliases_and_no_iso(self) -> None:
        index = load_countries_file()
        self.assertEqual(index.lookup("nauru").row.iso_country, "NRU")
        self.assertEqual(index.lookup("gambia-republic").row.iso_country, "GMB")
        self.assertEqual(index.lookup("republic-north-macedonia").row.iso_country, "MKD")
        self.assertEqual(index.lookup("palestine-state").row.iso_country, "PSE")
        self.assertEqual(index.lookup("netherlands-kingdom").row.iso_country, "NLD")
        self.assertEqual(index.lookup("cote-divoire").row.iso_country, "CIV")
        self.assertEqual(index.lookup("united-states-america").row.iso_country, "USA")
        self.assertEqual(
            index.lookup("united-kingdom-great-britain-and-northern-ireland").row.iso_country,
            "GBR",
        )
        self.assertTrue(index.lookup("european-union").expected_empty)
        self.assertTrue(index.lookup("secretary-general-united-nations").expected_empty)
        miss = index.lookup("not-a-real-country")
        self.assertIsNone(miss.row)
        self.assertFalse(miss.expected_empty)
        self.assertIn("sin match", miss.warning)

    def test_all_session_80_slugs_join(self) -> None:
        index = load_countries_file()
        missing: list[str] = []
        for slug in load_slugs(load_session("80")):
            match = index.lookup(slug)
            if slug in NO_ISO_SLUGS:
                self.assertTrue(match.expected_empty, slug)
                continue
            if not match.row or not match.row.iso_country:
                missing.append(slug)
        self.assertEqual(missing, [])


class MetadataRowTest(unittest.TestCase):
    def test_kenya_row(self) -> None:
        index = load_countries_file()
        speech = ExtractedSpeech(
            session_id=80,
            slug="kenya",
            country="Kenya",
            name="William Ruto",
            rank="President",
            speech_date="2025-09-24",
            source="pdf_en",
            source_url="https://example/ke_en.pdf",
            language="en",
            text="Excellencies",
            speaker_title="His Excellency",
            original_language="en",
            transformation="none",
        )
        row, warning = build_metadata_row(
            speech,
            countries=index,
            appearance=3,
            transcript_url="https://github.com/org/repo/blob/main/pipeline/out/80/2025-09-24/kenya.txt",
            ficha_url="https://gadebate.un.org/en/80/kenya",
        )
        self.assertEqual(warning, "")
        self.assertEqual(row.iso_country, "KEN")
        self.assertEqual(row.num_of_appearance, "3")
        self.assertEqual(row.date_time, "24/09/2025")
        self.assertEqual(row.source, "pdf_en")
        self.assertEqual(row.original_language, "english")
        self.assertEqual(row.transformation, "none")
        self.assertEqual(row.speaker_level, "HS")
        self.assertEqual(row.speaker_pronouns, "he/him")
        self.assertIn("William Ruto", row.speaker_name)
        self.assertEqual(infer_speaker_level("Minister for Foreign Affairs"), "CD")
        self.assertEqual(language_label("pt"), "portuguese")

    def test_speech_id_and_sheet_keys(self) -> None:
        self.assertEqual(next_speech_id(["M_1", "M_12", "x"]), 13)
        keys = existing_keys(
            [
                {
                    "ficha_url": "https://gadebate.un.org/en/80/kenya",
                    "date_time": "24/09/2025",
                }
            ]
        )
        self.assertIn("https://gadebate.un.org/en/80/kenya", keys)
        self.assertIn("kenya|24/09/2025", keys)

    def test_txt_roundtrip_and_local_csv(self) -> None:
        speech = ExtractedSpeech(
            session_id=80,
            slug="kenya",
            country="Kenya",
            name="William Ruto",
            rank="President",
            speech_date="2025-09-24",
            source="pdf_en",
            source_url="https://example/ke_en.pdf",
            language="en",
            text="Excellencies",
            speaker_title="His Excellency",
            original_language="sw",
            transformation="none",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kenya.txt"
            path.write_text(speech_to_txt(speech), encoding="utf-8")
            parsed = parse_speech_txt(path)
            self.assertEqual(parsed.original_language, "sw")
            self.assertEqual(parsed.speaker_title, "His Excellency")
            index = load_countries_file()
            row, _ = build_metadata_row(
                parsed, countries=index, appearance=1, ficha_url="https://x/kenya"
            )
            csv_path = Path(tmp) / "metadata.csv"
            write_metadata_csv(csv_path, [row])
            text = csv_path.read_text(encoding="utf-8")
            self.assertIn("iso_country", text)
            self.assertIn("KEN", text)
            self.assertNotIn("needed transformation", text.lower())

    def test_publish_day_writes_csv_without_sheet(self) -> None:
        from pipeline.publish import publish_day
        from pipeline.store import out_dir, write_speech

        config = load_session("80")
        speech = ExtractedSpeech(
            session_id=80,
            slug="kenya",
            country="Kenya",
            name="William Ruto",
            rank="President",
            speech_date="2025-09-24",
            source="pdf_en",
            source_url="https://example/ke_en.pdf",
            language="en",
            text="Excellencies",
            speaker_title="His Excellency",
            original_language="en",
            transformation="none",
        )
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            write_speech(speech, out_dir(config, speech.speech_date, dest=dest))
            code = publish_day(
                config,
                day="2025-09-24",
                dest=dest,
                do_github=False,
                do_sheet=True,
                write_csv=True,
            )
            self.assertEqual(code, 0)
            csv_path = dest / "80" / "2025-09-24" / "metadata.csv"
            self.assertTrue(csv_path.is_file())
            body = csv_path.read_text(encoding="utf-8")
            self.assertIn("M_1", body)
            self.assertIn("KEN", body)


class GithubUrlTest(unittest.TestCase):
    def test_parse_remote_and_blob_url(self) -> None:
        self.assertEqual(
            _parse_github_remote("git@github.com:org/gwl-bot.git"), "org/gwl-bot"
        )
        self.assertEqual(
            _parse_github_remote("https://github.com/org/gwl-bot.git"), "org/gwl-bot"
        )
        url = transcript_url_for(
            "pipeline/out/81/2026-09-22/kenya.txt",
            repo="org/gwl-bot",
            branch="main",
        )
        self.assertEqual(
            url,
            "https://github.com/org/gwl-bot/blob/main/pipeline/out/81/2026-09-22/kenya.txt",
        )
        with patch.dict(os.environ, {"GITHUB_TRANSCRIPT_STYLE": "raw"}):
            raw = transcript_url_for(
                "pipeline/out/81/2026-09-22/kenya.txt",
                repo="org/gwl-bot",
                branch="main",
            )
        self.assertTrue(raw.startswith("https://raw.githubusercontent.com/"))


class KalturaUrlTest(unittest.TestCase):
    def test_play_manifest(self) -> None:
        url = kaltura_play_url("1_abc", "2503451")
        self.assertIn("/p/2503451/", url)
        self.assertIn("entryId/1_abc", url)


if __name__ == "__main__":
    unittest.main()
