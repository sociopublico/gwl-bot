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
from pipeline.publish import (
    _git_push_cmd,
    _parse_github_remote,
    _redact_secret,
    transcript_url_for,
    write_metadata_csv,
)
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
        self.assertEqual(next_speech_id([]), 1)
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
            with patch("pipeline.sheets.can_write_sheets", return_value=False):
                with patch(
                    "pipeline.sheets.sheets_unavailable_reason",
                    return_value="test sin sheet",
                ):
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
            renamed = dest / "80" / "2025-09-24" / "M_1.txt"
            self.assertTrue(renamed.is_file())
            self.assertFalse((dest / "80" / "2025-09-24" / "kenya.txt").exists())
            parsed = parse_speech_txt(renamed)
            self.assertEqual(parsed.slug, "kenya")
            self.assertEqual(parsed.id_speech, "M_1")

    def test_publish_continues_ids_across_days(self) -> None:
        from pipeline.publish import publish_day
        from pipeline.store import out_dir, write_speech

        config = load_session("80")
        kenya = ExtractedSpeech(
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
        )
        finland = ExtractedSpeech(
            session_id=80,
            slug="finland",
            country="Finland",
            name="Alexander Stubb",
            rank="President",
            speech_date="2025-09-24",
            source="pdf_en",
            source_url="https://example/fi_en.pdf",
            language="en",
            text="Madam President",
        )
        finland.speech_date = "2025-09-25"
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            write_speech(kenya, out_dir(config, kenya.speech_date, dest=dest))
            with patch("pipeline.sheets.can_write_sheets", return_value=False):
                self.assertEqual(
                    publish_day(
                        config,
                        day="2025-09-24",
                        dest=dest,
                        do_github=False,
                        do_sheet=False,
                    ),
                    0,
                )
            write_speech(finland, out_dir(config, finland.speech_date, dest=dest))
            with patch("pipeline.sheets.can_write_sheets", return_value=False):
                self.assertEqual(
                    publish_day(
                        config,
                        day="2025-09-25",
                        dest=dest,
                        do_github=False,
                        do_sheet=False,
                    ),
                    0,
                )
            self.assertTrue((dest / "80" / "2025-09-24" / "M_1.txt").is_file())
            self.assertTrue((dest / "80" / "2025-09-25" / "M_2.txt").is_file())
            self.assertIn("M_2", (dest / "80" / "2025-09-25" / "metadata.csv").read_text())

    def test_publish_reuses_existing_file_id(self) -> None:
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
            id_speech="M_12",
        )
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            write_speech(speech, out_dir(config, speech.speech_date, dest=dest))
            with patch("pipeline.sheets.can_write_sheets", return_value=False):
                code = publish_day(
                    config,
                    day="2025-09-24",
                    dest=dest,
                    do_github=False,
                    do_sheet=False,
                )
            self.assertEqual(code, 0)
            self.assertTrue((dest / "80" / "2025-09-24" / "M_12.txt").is_file())
            self.assertFalse((dest / "80" / "2025-09-24" / "M_1.txt").exists())
            self.assertIn("M_12", (dest / "80" / "2025-09-24" / "metadata.csv").read_text())

    def test_publish_continues_ids_from_sheet(self) -> None:
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
        )
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            write_speech(speech, out_dir(config, speech.speech_date, dest=dest))
            with patch("pipeline.sheets.can_write_sheets", return_value=True):
                with patch(
                    "pipeline.sheets.read_metadata_records",
                    return_value=(
                        ["id_speech", "ficha_url"],
                        [{"id_speech": "M_40", "ficha_url": "https://other"}],
                    ),
                ):
                    with patch("pipeline.sheets.append_metadata_rows", return_value=[]):
                        code = publish_day(
                            config,
                            day="2025-09-24",
                            dest=dest,
                            do_github=False,
                            do_sheet=True,
                        )
            self.assertEqual(code, 0)
            self.assertTrue((dest / "80" / "2025-09-24" / "M_41.txt").is_file())
            self.assertIn("M_41", (dest / "80" / "2025-09-24" / "metadata.csv").read_text())


class DotenvSheetsTest(unittest.TestCase):
    def test_env_overrides_stale_google_credentials(self) -> None:
        from pipeline.env import load_dotenv

        with tempfile.TemporaryDirectory() as tmp:
            json_path = Path(tmp) / "sa.json"
            json_path.write_text("{}", encoding="utf-8")
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                f"GOOGLE_APPLICATION_CREDENTIALS={json_path}\n"
                "GOOGLE_SHEETS_SPREADSHEET_ID=abc123\n",
                encoding="utf-8",
            )
            stale = {
                "GOOGLE_APPLICATION_CREDENTIALS": "/no/existe.json",
                "GOOGLE_SHEETS_SPREADSHEET_ID": "old",
            }
            with patch.dict(os.environ, stale, clear=False):
                load_dotenv(env_path)
                self.assertEqual(os.environ["GOOGLE_APPLICATION_CREDENTIALS"], str(json_path))
                self.assertEqual(os.environ["GOOGLE_SHEETS_SPREADSHEET_ID"], "abc123")

    def test_keeps_container_credentials_if_dotenv_path_missing(self) -> None:
        from pipeline.env import load_dotenv

        with tempfile.TemporaryDirectory() as tmp:
            real_json = Path(tmp) / "container.json"
            real_json.write_text("{}", encoding="utf-8")
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "GOOGLE_APPLICATION_CREDENTIALS=/home/agus/host-only.json\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"GOOGLE_APPLICATION_CREDENTIALS": str(real_json)},
                clear=False,
            ):
                load_dotenv(env_path)
                self.assertEqual(os.environ["GOOGLE_APPLICATION_CREDENTIALS"], str(real_json))

    def test_keeps_docker_secrets_path_if_host_json_missing(self) -> None:
        from pipeline.env import load_dotenv

        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "GOOGLE_APPLICATION_CREDENTIALS=/home/agus/host-only.json\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"GOOGLE_APPLICATION_CREDENTIALS": "/secrets/google-sa.json"},
                clear=False,
            ):
                load_dotenv(env_path)
                creds = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
                self.assertNotIn("host-only.json", creds)

    def test_host_absolute_path_maps_to_repo_filename(self) -> None:
        from pipeline.env import _credential_candidates

        paths = _credential_candidates("/root/traefik/gwl-bot/gwl-bot-ab31f985cd4c.json")
        names = [p.name for p in paths]
        self.assertIn("gwl-bot-ab31f985cd4c.json", names)


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
        with patch("pipeline.publish.load_dotenv"):
            with patch.dict(os.environ, {"GITHUB_TRANSCRIPT_STYLE": "raw"}):
                raw = transcript_url_for(
                    "pipeline/out/81/2026-09-22/kenya.txt",
                    repo="org/gwl-bot",
                    branch="main",
                )
        self.assertTrue(raw.startswith("https://raw.githubusercontent.com/"))

    def test_push_uses_token_without_leaking_it(self) -> None:
        with patch.dict(
            os.environ,
            {"GITHUB_TOKEN": "secret-token", "GITHUB_REPO": "org/gwl-bot"},
            clear=False,
        ):
            cmd = _git_push_cmd(Path("."))
        self.assertEqual(cmd[0:2], ["git", "push"])
        self.assertIn("secret-token", cmd[2])
        self.assertIn("github.com/org/gwl-bot.git", cmd[2])
        self.assertEqual(
            _redact_secret("fatal: secret-token rejected", "secret-token"),
            "fatal: *** rejected",
        )


class KalturaUrlTest(unittest.TestCase):
    def test_play_manifest(self) -> None:
        url = kaltura_play_url("1_abc", "2503451")
        self.assertIn("/p/2503451/", url)
        self.assertIn("entryId/1_abc", url)


if __name__ == "__main__":
    unittest.main()
