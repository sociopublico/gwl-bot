from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.cascade import SourceUnavailable, choose_source
from pipeline.config import load_session, load_slugs
from pipeline.extract_audio import _join_segments
from pipeline.extract_pdf import clean_pdf_text
from pipeline.gadebate import parse_speaker_page, slugs_from_archive_html
from pipeline.models import FileRef, SpeakerPage
from pipeline.run import extract_from_page, language_for, select_slugs
from pipeline.store import speech_to_txt
from pipeline.models import ExtractedSpeech

FIXTURE = Path(__file__).parent / "fixtures" / "gadebate_brazil.html"


class SessionConfigTest(unittest.TestCase):
    def test_load_80_and_81(self) -> None:
        eighty = load_session("80")
        eighty_one = load_session("81")
        self.assertEqual(eighty.id, 80)
        self.assertEqual(eighty_one.id, 81)
        self.assertEqual(eighty.year, 2025)
        self.assertEqual(eighty_one.year, 2026)
        self.assertTrue(eighty.speaker_url("brazil").endswith("/en/80/brazil"))
        self.assertTrue(eighty_one.speaker_url("brazil").endswith("/en/81/brazil"))
        self.assertGreater(len(load_slugs(eighty)), 100)
        self.assertEqual(load_slugs(eighty_one), [])
        self.assertIn("2025-09-23", eighty.debate_dates)
        self.assertIn("2026-09-22", eighty_one.debate_dates)

    def test_day_filter_uses_index(self) -> None:
        config = load_session("80")
        slugs = select_slugs(config, day="2025-09-23")
        self.assertIn("brazil", slugs)
        self.assertGreaterEqual(len(slugs), 20)
        self.assertEqual(select_slugs(config, slug="kenya"), ["kenya"])


class ParsePageTest(unittest.TestCase):
    def test_brazil_fixture(self) -> None:
        config = load_session("80")
        html = FIXTURE.read_text(encoding="utf-8")
        page = parse_speaker_page(
            config, "https://gadebate.un.org/en/80/brazil", html
        )
        self.assertEqual(page.country, "Brazil")
        self.assertEqual(page.name, "Luiz Inácio Lula da Silva")
        self.assertEqual(page.rank, "President")
        self.assertEqual(page.speech_date, "2025-09-23")
        self.assertIsNone(page.pdf_en)
        self.assertIsNotNone(page.pdf_other)
        self.assertEqual(page.pdf_other.filename, "br_pt.pdf")
        self.assertIsNotNone(page.audio_en)
        self.assertEqual(page.audio_en.lang, "en")
        self.assertIsNotNone(page.audio_floor)
        self.assertEqual(page.video_entry_id, "1_abc")
        self.assertEqual(page.video_partner_id, "2503451")

    def test_cascade_skips_missing_pdf_en(self) -> None:
        page = SpeakerPage(
            slug="brazil",
            url="https://gadebate.un.org/en/80/brazil",
            country="Brazil",
            name="Lula",
            rank="President",
            speaker_title="",
            speech_date="2025-09-23",
            pdf_other=FileRef("as delivered", "https://x/br_pt.pdf", "br_pt.pdf"),
            audio_en=FileRef("english", "https://x/en.mp3", "80_BR_EN.mp3", "en"),
        )
        source, ref = choose_source(page, ("pdf_en", "audio_en", "pdf_other", "video"))
        self.assertEqual(source, "audio_en")
        source, ref = choose_source(page, ("pdf_en", "pdf_other", "audio_en"))
        self.assertEqual(source, "pdf_other")
        with self.assertRaises(SourceUnavailable):
            choose_source(page, ("pdf_en",))
        video_only = SpeakerPage(
            slug="norway",
            url="https://gadebate.un.org/en/80/norway",
            country="Norway",
            name="Jonas Gahr Støre",
            rank="Prime Minister",
            speaker_title="",
            speech_date="2025-09-23",
            video_entry_id="1_nor",
            video_partner_id="2503451",
        )
        source, ref = choose_source(
            video_only, ("pdf_en", "audio_en", "pdf_other", "video")
        )
        self.assertEqual(source, "video")
        self.assertIn("1_nor", ref.url)
        self.assertIn("2503451", ref.url)

    def test_archive_slugs_are_session_scoped(self) -> None:
        config = load_session("80")
        html = """
        <a href="/en/80/brazil">Brazil</a>
        <a href="/en/81/kenya">Kenya</a>
        <a href="/en/80/france">France</a>
        """
        self.assertEqual(slugs_from_archive_html(config, html), ["brazil", "france"])

    def test_txt_header(self) -> None:
        text = speech_to_txt(
            ExtractedSpeech(
                session_id=80,
                slug="kenya",
                country="Kenya",
                name="William Ruto",
                rank="President",
                speech_date="2025-09-24",
                source="pdf_en",
                source_url="https://example/ke_en.pdf",
                language="en",
                text="Excellencies,\nWe commit to 1.5.",
            )
        )
        self.assertIn("session: 80", text)
        self.assertIn("source: pdf_en", text)
        self.assertTrue(text.strip().endswith("We commit to 1.5."))

    def test_language_for_other_pdf(self) -> None:
        ref = FileRef("Statement in French", "https://x/fr_fr.pdf", "fr_fr.pdf")
        self.assertEqual(language_for("pdf_other", ref), "fr")
        self.assertEqual(
            language_for("pdf_en", FileRef("en", "https://x/ke_en.pdf", "ke_en.pdf")),
            "en",
        )

    def test_whisper_joins_pauses_as_paragraphs(self) -> None:
        class Seg:
            def __init__(self, start: float, end: float, text: str) -> None:
                self.start = start
                self.end = end
                self.text = text

        text = _join_segments(
            [Seg(0, 1, "Hello,"), Seg(1.1, 2, "world."), Seg(4, 5, "Next.")]
        )
        self.assertEqual(text, "Hello, world.\n\nNext.")

    def test_audio_en_when_no_pdf(self) -> None:
        config = load_session("80")
        page = SpeakerPage(
            slug="united-states-america",
            url="https://gadebate.un.org/en/80/united-states-america",
            country="United States of America",
            name="Donald Trump",
            rank="President",
            speaker_title="",
            speech_date="2025-09-23",
            audio_en=FileRef(
                "english",
                "https://x/test_fake_US_EN.mp3",
                "test_fake_US_EN.mp3",
                "en",
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            with patch("pipeline.run.fetch", return_value=(200, {}, b"fake-mp3")):
                with patch(
                    "pipeline.run.transcribe_audio_file",
                    return_value="Madam President, " * 20,
                ):
                    speech = extract_from_page(config, page, dest=Path(tmp))
        self.assertEqual(speech.source, "audio_en")
        self.assertEqual(speech.language, "en")
        self.assertEqual(speech.original_language, "en")
        self.assertEqual(speech.transformation, "whisper")
        self.assertIn("Madam President", speech.text)

    def test_original_language_from_pdf_other(self) -> None:
        from pipeline.run import original_language_for

        config = load_session("80")
        page = SpeakerPage(
            slug="brazil",
            url="https://gadebate.un.org/en/80/brazil",
            country="Brazil",
            name="Lula",
            rank="President",
            speaker_title="His Excellency",
            speech_date="2025-09-23",
            pdf_other=FileRef("as delivered", "https://x/br_pt.pdf", "br_pt.pdf"),
            audio_en=FileRef("english", "https://x/en.mp3", "80_BR_EN.mp3", "en"),
        )
        self.assertEqual(original_language_for(page), "pt")
        with tempfile.TemporaryDirectory() as tmp:
            with patch("pipeline.run.fetch", return_value=(200, {}, b"fake-mp3")):
                with patch(
                    "pipeline.run.transcribe_audio_file",
                    return_value="Madam President, " * 20,
                ):
                    speech = extract_from_page(config, page, dest=Path(tmp))
        self.assertEqual(speech.source, "audio_en")
        self.assertEqual(speech.language, "en")
        self.assertEqual(speech.original_language, "pt")
        self.assertEqual(speech.transformation, "whisper")

    def test_video_last_resort(self) -> None:
        config = load_session("80")
        page = SpeakerPage(
            slug="norway",
            url="https://gadebate.un.org/en/80/norway",
            country="Norway",
            name="Jonas Gahr Støre",
            rank="Prime Minister",
            speaker_title="His Excellency",
            speech_date="2025-09-23",
            video_entry_id="1_nor",
        )
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "pipeline.extract_video.transcribe_kaltura",
                return_value="Madam President, " * 20,
            ):
                speech = extract_from_page(config, page, dest=Path(tmp))
        self.assertEqual(speech.source, "video")
        self.assertEqual(speech.transformation, "whisper")
        self.assertIn("kaltura.com", speech.source_url)

    def test_journal_order_beats_date_index(self) -> None:
        from dataclasses import replace

        config = load_session("80")
        with tempfile.TemporaryDirectory() as tmp:
            journal_dir = Path(tmp)
            (journal_dir / "2025-09-23.txt").write_text(
                "# UN Journal\nkenya\nbrazil\n", encoding="utf-8"
            )
            config = replace(config, journal_dir=journal_dir)
            self.assertEqual(
                select_slugs(config, day="2025-09-23"), ["kenya", "brazil"]
            )

    def test_session_cascade_puts_video_last(self) -> None:
        config = load_session("81")
        self.assertEqual(
            config.sources, ("pdf_en", "audio_en", "pdf_other", "video")
        )


class PdfCleanTest(unittest.TestCase):
    def test_indonesia_drops_intro_headers_and_unwraps(self) -> None:
        raw = """
Page 1 of 7 | vDelivered
President Prabowo Subianto:
Indonesia's Call for Hope
Remarks Delivered at the United Nations General Assembly
September 23 2025

Bismillahirrahmanirrahim,
Assalamu'alaikum warahmatullahi wabarakatuh.
Shalom, Salve, Om swastiastu,
Salam kebajikan, Rahayu, rahayu.

Madam President, distinguished delegates, excellencies ,

It is indeed a great honor to stand in this august General Assembly Hall, among
leaders who represent almost all of humanity.

We differ in race, religion, and nationality, yet we gather  together as one human
family.

We are here first and foremost as fellow human beings  — each created equal,
endowed with unalienable rights to life, liberty, and the pursuit of happiness.

The words of the U.S. Declaration of Independence have inspired democratic
movements  across continents  — including the French Revolution, the Russian
Revolution, the Chinese Revolution, and Indonesia’s own journey to freedo m.

 Page 2 of 7 | vDelivered
And yet, in our own era of scientific and technological triumphs — an era capable of
ending hunger, poverty, and environmental ruin  — we also continue to face grave
challenges  and uncertainties.
"""
        text = clean_pdf_text(raw)
        self.assertNotIn("Page 1 of 7", text)
        self.assertNotIn("vDelivered", text)
        self.assertNotIn("Indonesia's Call for Hope", text)
        self.assertNotIn("Bismillahirrahmanirrahim", text)
        self.assertTrue(text.startswith("Madam President"))
        self.assertIn(
            "yet we gather together as one human family.",
            text,
        )
        self.assertIn(
            "endowed with unalienable rights to life, liberty, and the pursuit of happiness.",
            text.split("one human family.")[1],
        )
        yet = [p for p in text.split("\n\n") if p.startswith("And yet")][0]
        self.assertNotIn("\n", yet)
        self.assertIn("grave challenges and uncertainties.", yet)
        russian = [p for p in text.split("\n\n") if "French Revolution" in p][0]
        self.assertIn("the Russian Revolution, the Chinese Revolution", russian)
        self.assertNotIn("\n", russian)

    def test_kenya_page_markers(self) -> None:
        raw = """
STATEMENT BY
HIS EXCELLENCY WILLIAM S. RUTO PHD., C.G.H,
Please check against delivery
2 | P a g e
The President of the 80th Session of the UN General Assembly, Ms
Annalena Baerbock,
1. I congratulate you, Ms Annalena Baerbock, on your election to
preside over the 80th Session of the UN General Assembly.
3 | P a g e
2. Ladies and gentlemen,  80 years ago, in the aftermath of
unprecedented global destruction and devastating war, the international
community came together in hope.
"""
        text = clean_pdf_text(raw)
        self.assertNotIn("P a g e", text)
        self.assertNotIn("Please check against delivery", text)
        self.assertIn("I congratulate you, Ms Annalena Baerbock, on your election to preside over", text)
        self.assertIn("Ms Annalena Baerbock", text)
        self.assertNotIn("\nAnnalena", text)
        self.assertIn("\n\n2. Ladies and gentlemen", text)


if __name__ == "__main__":
    unittest.main()
