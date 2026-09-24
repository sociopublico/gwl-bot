from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from pipeline.config import load_session
from pipeline.models import ExtractedSpeech, FileRef, SpeakerPage
from pipeline.replay_alert import run_replay


def _config(root: Path):
    return replace(load_session("81"), root=root)


def _roster(root: Path, day: str, speakers: list[dict]) -> None:
    path = root / "data" / "roster" / "81" / f"{day}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"session": 81, "day": day, "speakers": speakers}),
        encoding="utf-8",
    )


def _pdf(url: str = "https://example.test/uy.pdf") -> dict:
    return {
        "label": "en",
        "url": url,
        "filename": "uy.pdf",
        "lang": "en",
    }


class ReplayAlertTest(unittest.TestCase):
    def test_matches_country_and_source_without_sending(self) -> None:
        words = " ".join(f"w{i}" for i in range(40))
        text = f"{words} multilateralism {words}"
        extracted: list[str] = []
        scraped: list[str] = []
        sent: list[str] = []

        def extract(config, page, source):
            extracted.append(source)
            return (
                ExtractedSpeech(
                    session_id=81,
                    slug=page.slug,
                    country=page.country,
                    name=page.name,
                    rank=page.rank,
                    speech_date=page.speech_date,
                    source=source,
                    source_url="https://example.test/uy.pdf",
                    language="en",
                    text=text,
                ),
                "pdf",
            )

        def scrape(config, slug):
            scraped.append(slug)
            raise AssertionError("no debería bajar la ficha")

        def sender(subject, body, html):
            sent.append(subject)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _roster(
                root,
                "2026-09-24",
                [
                    {
                        "slug": "uruguay",
                        "country": "Uruguay",
                        "name": "Yamandú Orsi",
                        "rank": "President",
                        "speech_date": "2026-09-24",
                        "sources": {"pdf_en": _pdf(), "transcript_ai": None},
                    }
                ],
            )
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = run_replay(
                    _config(root),
                    country="uruguay",
                    source="pdf_en",
                    day="2026-09-24",
                    keywords=("multilateralism",),
                    send=False,
                    before_seconds=60 / 140 * 3,
                    after_seconds=60 / 140 * 2,
                    extract=extract,
                    scrape=scrape,
                    sender=sender,
                )
        self.assertEqual(code, 0)
        self.assertEqual(extracted, ["pdf_en"])
        self.assertEqual(scraped, [])
        self.assertEqual(sent, [])
        mail = stdout.getvalue()
        self.assertIn("Replay | source=pdf_en", mail)
        self.assertIn("**multilateralism**", mail)
        self.assertIn("Yamandú Orsi", mail)
        self.assertIn("w39 **multilateralism**", mail)
        self.assertNotIn("w10", mail)

    def test_scrapes_when_source_is_missing(self) -> None:
        extracted: list[str] = []

        def scrape(config, slug):
            return SpeakerPage(
                slug=slug,
                url="https://gadebate.un.org/en/81/uruguay",
                country="Uruguay",
                name="Yamandú Orsi",
                rank="",
                speaker_title="",
                speech_date="2026-09-24",
                transcript_ai=FileRef("ai", "https://example.test/uy.txt", "uy.txt", "en"),
            )

        def extract(config, page, source):
            extracted.append(source)
            self.assertIsNotNone(page.transcript_ai)
            return (
                ExtractedSpeech(
                    session_id=81,
                    slug=page.slug,
                    country=page.country,
                    name=page.name,
                    rank="",
                    speech_date=page.speech_date,
                    source=source,
                    source_url=page.transcript_ai.url,
                    language="en",
                    text="No keywords here.",
                ),
                "ai_transcript",
            )

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _roster(
                root,
                "2026-09-24",
                [
                    {
                        "slug": "uruguay",
                        "country": "Uruguay",
                        "name": "Yamandú Orsi",
                        "speech_date": "2026-09-24",
                        "sources": {"pdf_en": None, "transcript_ai": None},
                    }
                ],
            )
            stderr = io.StringIO()
            with redirect_stderr(stderr), redirect_stdout(io.StringIO()):
                code = run_replay(
                    _config(root),
                    country="Uruguay",
                    source="transcript_ai",
                    day="2026-09-24",
                    keywords=("multilateralism",),
                    send=False,
                    extract=extract,
                    scrape=scrape,
                    sender=lambda subject, body, html: self.fail("no manda"),
                )
        self.assertEqual(code, 0)
        self.assertEqual(extracted, ["transcript_ai"])
        self.assertIn("No hay keywords", stderr.getvalue())

    def test_rejects_audio_source(self) -> None:
        with TemporaryDirectory() as tmp:
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                code = run_replay(
                    _config(Path(tmp)),
                    country="uruguay",
                    source="audio_en",
                    day="2026-09-24",
                    keywords=("women",),
                    send=True,
                    sender=lambda subject, body, html: self.fail("no manda"),
                )
        self.assertEqual(code, 1)
        self.assertIn("pdf_en o transcript_ai", stderr.getvalue())
