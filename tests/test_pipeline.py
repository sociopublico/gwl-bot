from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.cascade import SourceUnavailable, choose_source
from pipeline.config import coerce_debate_day, load_session, load_slugs
from pipeline.extract_audio import _join_segments
from pipeline.extract_ocr import ocr_pdf_text, tesseract_lang_for
from pipeline.extract_pdf import clean_pdf_text, is_cid_garbage, strip_assembly_protocol
from pipeline.gadebate import (
    listings_from_homepage_html,
    parse_speaker_page,
    scrape_speaker,
    slug_catalog_for,
    slug_from_speaker_title,
    slugs_from_archive_html,
)
from pipeline.models import FileRef, SpeakerPage
from pipeline.run import extract_from_page, extract_from_page_timed, fetch_speeches, language_for, select_slugs
from pipeline.store import speech_to_txt
from pipeline.models import ExtractedSpeech

FIXTURE = Path(__file__).parent / "fixtures" / "gadebate_brazil.html"
HOME = Path(__file__).parent / "fixtures" / "gadebate_home.html"


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
        self.assertTrue(eighty_one.homepage_url().endswith("/en"))
        self.assertEqual(
            coerce_debate_day(eighty_one, "2025-09-22"), "2026-09-22"
        )
        self.assertEqual(
            coerce_debate_day(eighty_one, "2026-09-22"), "2026-09-22"
        )
        self.assertEqual(
            coerce_debate_day(eighty, "2026-09-23"), "2025-09-23"
        )

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
        self.assertIsNone(page.transcript_ai)

    def test_parses_ai_transcript_prepare_download(self) -> None:
        config = load_session("81")
        html = """
        <title>Brazil | 81st session</title>
        <a href="/en/node/81024/transcript/en/prepare-download"
           class="download-transcript btn btn-secondary"
           data-un-gad-transcript-download>Transcript (AI generated)</a>
        """
        page = parse_speaker_page(
            config, "https://gadebate.un.org/en/81/brazil", html
        )
        assert page.transcript_ai is not None
        self.assertEqual(page.transcript_ai.lang, "en")
        self.assertTrue(
            page.transcript_ai.url.endswith(
                "/en/node/81024/transcript/en/prepare-download"
            )
        )
        self.assertEqual(page.transcript_ai.filename, "81-brazil-en-transcript.txt")

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

    def test_cascade_prefers_transcript_ai_over_audio(self) -> None:
        page = SpeakerPage(
            slug="brazil",
            url="https://gadebate.un.org/en/81/brazil",
            country="Brazil",
            name="Lula",
            rank="President",
            speaker_title="",
            speech_date="2026-09-22",
            transcript_ai=FileRef(
                "transcript ai",
                "https://gadebate.un.org/en/node/81024/transcript/en/prepare-download",
                "81-brazil-en-transcript.txt",
                "en",
            ),
            audio_en=FileRef("english", "https://x/en.mp3", "81_BR_EN.mp3", "en"),
        )
        source, ref = choose_source(
            page, ("pdf_en", "transcript_ai", "audio_en", "pdf_other", "video")
        )
        self.assertEqual(source, "transcript_ai")
        self.assertIn("prepare-download", ref.url)

    def test_archive_slugs_are_session_scoped(self) -> None:
        config = load_session("80")
        html = """
        <a href="/en/80/brazil">Brazil</a>
        <a href="/en/81/kenya">Kenya</a>
        <a href="/en/80/france">France</a>
        """
        self.assertEqual(slugs_from_archive_html(config, html), ["brazil", "france"])

    def test_homepage_listings_map_morning_afternoon_to_slugs(self) -> None:
        config = load_session("81")
        html = HOME.read_text(encoding="utf-8")
        listed_day, speakers = listings_from_homepage_html(
            html, slug_catalog_for(config)
        )
        self.assertEqual(listed_day, "2026-09-22")
        slugs = [item.slug for item in speakers]
        self.assertEqual(
            slugs,
            [
                "secretary-general-united-nations",
                "president-general-assembly-opening",
                "brazil",
                "united-states-america",
                "naoero",
                "united-kingdom-great-britain-and-northern-ireland",
                "republic-korea",
                "belgium",
                "morocco",
            ],
        )
        self.assertEqual(speakers[0].part, "morning")
        self.assertEqual(speakers[0].name, "António Guterres")
        self.assertEqual(speakers[1].slug, "president-general-assembly-opening")
        self.assertEqual(speakers[1].name, "Dr. Khalilur Rahman")
        self.assertEqual(speakers[-1].part, "afternoon")
        self.assertEqual(speakers[-1].title, "Morocco")
        self.assertEqual(speakers[-2].slug, "belgium")
        self.assertEqual(speakers[-2].name, "Maxime Prévot")

    def test_institutional_listing_titles_map_to_slugs(self) -> None:
        catalog = slug_catalog_for(load_session("81"))
        self.assertEqual(
            slug_from_speaker_title(
                "1. Secretary-General of the United Nations", catalog
            ),
            "secretary-general-united-nations",
        )
        self.assertEqual(
            slug_from_speaker_title(
                "2. President of the General Assembly (opening)", catalog
            ),
            "president-general-assembly-opening",
        )
        self.assertEqual(
            slug_from_speaker_title("President of the General Assembly", catalog),
            "president-general-assembly-opening",
        )
        self.assertEqual(
            slug_from_speaker_title(
                "President of the General Assembly (closing)", catalog
            ),
            "president-general-assembly-closing",
        )
        self.assertEqual(slug_from_speaker_title("Naoero", catalog), "naoero")

    def test_session_81_journal_starts_with_sg_and_pga(self) -> None:
        config = load_session("81")
        slugs = select_slugs(config, day="2026-09-22")
        self.assertGreaterEqual(len(slugs), 2)
        self.assertEqual(
            slugs[:2],
            [
                "secretary-general-united-nations",
                "president-general-assembly-opening",
            ],
        )

    def test_empty_html_is_marked_unusable(self) -> None:
        config = load_session("80")
        with patch(
            "pipeline.gadebate.fetch",
            return_value=(200, {}, b"<html><title>Access Denied</title></html>"),
        ):
            page = scrape_speaker(config, "brazil")
        self.assertIsNotNone(page.error)
        self.assertIn("ficha vacía HTTP 200", page.error or "")
        self.assertIn("Access Denied", page.error or "")

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

    def test_non_english_txt_is_not_skipped(self) -> None:
        from pipeline.store import is_english_transcript, write_speech

        spanish = ExtractedSpeech(
            session_id=80,
            slug="chile",
            country="Chile",
            name="Gabriel Boric Font",
            rank="President",
            speech_date="2025-09-23",
            source="pdf_other",
            source_url="https://x/cl_es.pdf",
            language="es",
            text="Estimada presidenta",
            original_language="es",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chile.txt"
            path.write_text(speech_to_txt(spanish), encoding="utf-8")
            self.assertFalse(is_english_transcript(path))
            english = write_speech(
                ExtractedSpeech(
                    session_id=80,
                    slug="kenya",
                    country="Kenya",
                    name="William Ruto",
                    rank="President",
                    speech_date="2025-09-24",
                    source="pdf_en",
                    source_url="https://x/ke.pdf",
                    language="en",
                    text="Excellencies",
                ),
                Path(tmp),
            )
            self.assertTrue(is_english_transcript(english))

    def test_write_speech_keeps_id_filename_on_overwrite(self) -> None:
        from pipeline.store import parse_speech_txt, write_speech

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            first = write_speech(
                ExtractedSpeech(
                    session_id=80,
                    slug="kenya",
                    country="Kenya",
                    name="William Ruto",
                    rank="President",
                    speech_date="2025-09-24",
                    source="pdf_en",
                    source_url="https://x/ke.pdf",
                    language="en",
                    text="Excellencies",
                    id_speech="M_4",
                ),
                directory,
            )
            self.assertEqual(first.name, "M_4.txt")
            again = write_speech(
                ExtractedSpeech(
                    session_id=80,
                    slug="kenya",
                    country="Kenya",
                    name="William Ruto",
                    rank="President",
                    speech_date="2025-09-24",
                    source="pdf_en",
                    source_url="https://x/ke.pdf",
                    language="en",
                    text="Updated",
                ),
                directory,
            )
            self.assertEqual(again.name, "M_4.txt")
            self.assertFalse((directory / "kenya.txt").exists())
            parsed = parse_speech_txt(again)
            self.assertEqual(parsed.id_speech, "M_4")
            self.assertEqual(parsed.text, "Updated")

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

    def test_transcript_ai_before_audio_en(self) -> None:
        from dataclasses import replace

        from pipeline.extract_transcript import clean_ai_transcript

        raw = """Meeting/Event: Brazil - General Debate, 81st Session
Date: 22 September 2026
[Auto-generated transcript: may contain errors]

The Assembly will now hear an address by His Excellency Luiz Inacio Lula da Silva, president of the Federative Republic of Brazil.
I request protocol to escort His Excellency and invite him to address the Assembly.

Madam President, Brazil remains committed to multilateralism and peace.
We will keep working with all nations represented in this hall to strengthen
the United Nations and to defend diplomacy as the only path that can last.

On behalf of the Assembly, I wish to thank the president of the Federative Republic of Brazil.
"""
        cleaned = clean_ai_transcript(raw)
        self.assertIn("Brazil remains committed", cleaned)
        self.assertNotIn("Meeting/Event:", cleaned)
        self.assertNotIn("Auto-generated transcript", cleaned)
        self.assertNotIn("I wish to thank", cleaned)

        page = SpeakerPage(
            slug="brazil",
            url="https://gadebate.un.org/en/81/brazil",
            country="Brazil",
            name="Lula",
            rank="President",
            speaker_title="",
            speech_date="2026-09-22",
            transcript_ai=FileRef(
                "transcript ai",
                "https://gadebate.un.org/en/node/81024/transcript/en/prepare-download",
                "81-brazil-en-transcript.txt",
                "en",
            ),
            audio_en=FileRef("english", "https://x/en.mp3", "81_BR_EN.mp3", "en"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            config = replace(load_session("81"), root=Path(tmp))
            with patch(
                "pipeline.extract_transcript.download_ai_transcript",
                return_value=raw,
            ), patch(
                "pipeline.run.transcribe_audio_file",
                side_effect=AssertionError("no debería transcribir audio"),
            ):
                speech = extract_from_page(config, page, dest=Path(tmp))
        self.assertEqual(speech.source, "transcript_ai")
        self.assertEqual(speech.via, "ai_transcript")
        self.assertEqual(speech.transformation, "none")
        self.assertIn("Brazil remains committed", speech.text)

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

    def test_pdf_other_is_translated_to_english(self) -> None:
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
        )
        portuguese = ("Senhoras e senhores, o Brasil fala hoje. " * 12).strip()
        english = ("Ladies and gentlemen, Brazil speaks today. " * 12).strip()
        with tempfile.TemporaryDirectory() as tmp:
            with patch("pipeline.run.fetch", return_value=(200, {}, b"%PDF-fake")):
                with patch("pipeline.run.extract_pdf_text", return_value=portuguese):
                    with patch(
                        "pipeline.translate.translate_to_english",
                        return_value=english,
                    ) as mocked:
                        speech = extract_from_page(config, page, dest=Path(tmp))
        mocked.assert_called_once()
        self.assertEqual(mocked.call_args.kwargs.get("source_lang"), "pt")
        self.assertEqual(speech.source, "pdf_other")
        self.assertEqual(speech.language, "en")
        self.assertEqual(speech.original_language, "pt")
        self.assertEqual(speech.transformation, "translate")
        self.assertIn("Brazil speaks today", speech.text)

    def test_non_english_video_is_translated(self) -> None:
        from dataclasses import replace

        config = replace(load_session("80"), sources=("video",))
        page = SpeakerPage(
            slug="brazil",
            url="https://gadebate.un.org/en/80/brazil",
            country="Brazil",
            name="Lula",
            rank="President",
            speaker_title="His Excellency",
            speech_date="2025-09-23",
            pdf_other=FileRef("as delivered", "https://x/br_pt.pdf", "br_pt.pdf"),
            video_entry_id="1_br",
        )
        english = ("Ladies and gentlemen, Brazil speaks today. " * 12).strip()
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "pipeline.extract_video.transcribe_kaltura",
                return_value="Senhoras e senhores, " * 20,
            ):
                with patch(
                    "pipeline.translate.translate_to_english",
                    return_value=english,
                ) as mocked:
                    speech = extract_from_page(config, page, dest=Path(tmp))
        mocked.assert_called_once()
        self.assertEqual(speech.source, "video")
        self.assertEqual(speech.language, "en")
        self.assertEqual(speech.original_language, "pt")
        self.assertEqual(speech.transformation, "translate")

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
            config.sources,
            ("pdf_en", "transcript_ai", "audio_en", "pdf_other", "video"),
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

    def test_strips_pga_protocol_from_whisper(self) -> None:
        raw = (
            "The Assembly will hear and address by his excellency, Louis Nassio Lula da Silva, "
            "President of the Federal Radio, Republic of Brazil. I request the protocol to escort "
            "his excellency and invite him to address the Assembly. Madam President of the General "
            "Assembly, Anadina Berbuki, Mr. Secretary General. This should be a time to celebrate. "
            "May God bless us all. And thank you very much. On behalf of the Assembly, I wish to "
            "thank the President of the Federative Republic of Brazil."
        )
        text = strip_assembly_protocol(raw)
        self.assertTrue(text.startswith("Madam President"))
        self.assertNotIn("The Assembly will hear", text)
        self.assertNotIn("request the protocol", text)
        self.assertTrue(text.endswith("And thank you very much."))
        self.assertNotIn("On behalf of the Assembly", text)

        poland = (
            "Assembly will now hear an address by his Excellency, Carl, president of Poland.\n\n"
            "I request protocol to escort his Excellency and invite him to address the Assembly. "
            "Ladies and gentlemen, I am standing here."
        )
        text = strip_assembly_protocol(poland)
        self.assertTrue(text.startswith("Ladies and gentlemen"))
        self.assertNotIn("Assembly will now hear", text)

        us = (
            "The Assembly will hear an address by his excellency Donald Trump, "
            "President of the United States of America. I request Protocol to his Court his "
            "excellency and invite him to address the assembly. Thank you very much."
        )
        self.assertTrue(strip_assembly_protocol(us).startswith("Thank you very much."))

        speech = "Madam President, distinguished delegates, we gather as one."
        self.assertEqual(strip_assembly_protocol(speech), speech)

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

    def test_cid_font_layer_is_garbage(self) -> None:
        raw = (
            "/0/1/2/3\n/4/5/5/6/7/8/8/i255/10/11/i255/12/13/14/15/16/15/8/i255/"
            "17/15/18/8/19/5/20/21/i255/22/23/24/25/26"
        )
        self.assertTrue(is_cid_garbage(raw))
        self.assertFalse(is_cid_garbage("Madam President, distinguished delegates."))

    def test_drops_browser_print_header(self) -> None:
        raw = """
Mister President,

9/24/25, 11:14 PM Address by Gitanas Nausėda, President of the Republic of Lithuania | Permanent Mission

News

Excellencies, we gather here today to defend the Charter.
"""
        text = clean_pdf_text(raw)
        self.assertTrue(text.startswith("Mister President"))
        self.assertNotIn("11:14 PM", text)
        self.assertNotIn("News", text)
        self.assertIn("defend the Charter", text)

    def test_unreadable_pdf_en_uses_ocr(self) -> None:
        config = load_session("80")
        page = SpeakerPage(
            slug="lithuania",
            url="https://gadebate.un.org/en/80/lithuania",
            country="Lithuania",
            name="Gitanas Nausėda",
            rank="President",
            speaker_title="His Excellency",
            speech_date="2025-09-23",
            pdf_en=FileRef("Statement in English", "https://x/lt_en.pdf", "lt_en.pdf"),
            audio_en=FileRef("english", "https://x/80_LT_EN.mp3", "80_LT_EN.mp3", "en"),
        )
        ocr_text = "Madam President, distinguished delegates. " * 12
        with tempfile.TemporaryDirectory() as tmp:
            with patch("pipeline.run.fetch", return_value=(200, {}, b"%PDF-fake")):
                with patch("pipeline.run.extract_pdf_text", return_value=""):
                    with patch("pipeline.run.ocr_pdf_text", return_value=ocr_text) as mocked:
                        speech = extract_from_page(config, page, dest=Path(tmp))
        mocked.assert_called_once()
        self.assertEqual(mocked.call_args.kwargs.get("lang"), "eng")
        self.assertEqual(speech.source, "pdf_en")
        self.assertEqual(speech.transformation, "ocr")
        self.assertIn("Madam President", speech.text)

    def test_extract_from_page_timed_reports_via_and_elapsed(self) -> None:
        config = load_session("80")
        page = SpeakerPage(
            slug="lithuania",
            url="https://gadebate.un.org/en/80/lithuania",
            country="Lithuania",
            name="Gitanas Nausėda",
            rank="President",
            speaker_title="His Excellency",
            speech_date="2025-09-23",
            pdf_en=FileRef("Statement in English", "https://x/lt_en.pdf", "lt_en.pdf"),
            audio_en=FileRef("english", "https://x/80_LT_EN.mp3", "80_LT_EN.mp3", "en"),
        )
        ocr_text = "Madam President, distinguished delegates. " * 12
        with tempfile.TemporaryDirectory() as tmp:
            with patch("pipeline.run.fetch", return_value=(200, {}, b"%PDF-fake")):
                with patch("pipeline.run.extract_pdf_text", return_value=""):
                    with patch("pipeline.run.ocr_pdf_text", return_value=ocr_text):
                        speech, via, elapsed = extract_from_page_timed(
                            config, page, dest=Path(tmp)
                        )
        self.assertEqual(speech.source, "pdf_en")
        self.assertEqual(via, "ocr")
        self.assertGreaterEqual(elapsed, 0.0)

    def test_fetch_uses_roster_without_scrape(self) -> None:
        from pipeline.roster import speaker_entry_from_page

        config = load_session("80")
        html = FIXTURE.read_text(encoding="utf-8")
        page = parse_speaker_page(
            config, "https://gadebate.un.org/en/80/brazil", html
        )
        roster = {
            "session": 80,
            "day": "2025-09-23",
            "speakers": [speaker_entry_from_page(page, config.sources)],
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch("pipeline.roster.load_roster", return_value=roster):
                with patch("pipeline.run.scrape_speaker") as scrape:
                    with patch("pipeline.run.fetch", return_value=(200, {}, b"fake-mp3")):
                        with patch(
                            "pipeline.run.transcribe_audio_file",
                            return_value="Madam President, " * 20,
                        ):
                            results = fetch_speeches(
                                config,
                                day="2025-09-23",
                                slug="brazil",
                                dest=Path(tmp),
                                require_roster=True,
                            )
        scrape.assert_not_called()
        self.assertEqual(len(results), 1)
        self.assertIsNotNone(results[0].speech)
        self.assertEqual(results[0].speech.source, "audio_en")
        self.assertEqual(results[0].via, "whisper")
        self.assertGreaterEqual(results[0].elapsed_s, 0.0)

    def test_skip_existing_honors_reextract(self) -> None:
        from pipeline.roster import speaker_entry_from_page
        from pipeline.store import out_dir, write_speech

        config = load_session("80")
        html = FIXTURE.read_text(encoding="utf-8")
        page = parse_speaker_page(
            config, "https://gadebate.un.org/en/80/brazil", html
        )
        roster = {
            "session": 80,
            "day": "2025-09-23",
            "speakers": [speaker_entry_from_page(page, config.sources)],
        }
        old = ExtractedSpeech(
            session_id=80,
            slug="brazil",
            country="Brazil",
            name="Lula",
            rank="President",
            speech_date="2025-09-23",
            source="audio_en",
            source_url="https://example/en.mp3",
            language="en",
            text="Old english transcript " * 20,
        )
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            write_speech(old, out_dir(config, "2025-09-23", dest=dest))
            with patch("pipeline.roster.load_roster", return_value=roster):
                skipped = fetch_speeches(
                    config,
                    day="2025-09-23",
                    slug="brazil",
                    dest=dest,
                    skip_existing=True,
                    require_roster=True,
                )
                self.assertEqual(skipped[0].skip, "already extracted")
                with patch("pipeline.run.fetch", return_value=(200, {}, b"fake-mp3")):
                    with patch(
                        "pipeline.run.transcribe_audio_file",
                        return_value="New english transcript " * 20,
                    ) as transcribed:
                        redone = fetch_speeches(
                            config,
                            day="2025-09-23",
                            slug="brazil",
                            dest=dest,
                            skip_existing=True,
                            require_roster=True,
                            reextract={"brazil"},
                        )
            transcribed.assert_called()
            self.assertIsNotNone(redone[0].speech)
            self.assertIn("New english", redone[0].speech.text)

    def test_extract_require_roster_missing_file(self) -> None:
        config = load_session("80")
        with patch("pipeline.roster.load_roster", return_value=None):
            with self.assertRaises(FileNotFoundError):
                fetch_speeches(
                    config,
                    day="2025-09-23",
                    require_roster=True,
                )

    def test_extract_require_roster_unknown_slug(self) -> None:
        from pipeline.roster import speaker_entry_from_page

        config = load_session("80")
        html = FIXTURE.read_text(encoding="utf-8")
        page = parse_speaker_page(
            config, "https://gadebate.un.org/en/80/brazil", html
        )
        roster = {
            "session": 80,
            "day": "2025-09-23",
            "speakers": [speaker_entry_from_page(page, config.sources)],
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch("pipeline.roster.load_roster", return_value=roster):
                with patch("pipeline.run.scrape_speaker") as scrape:
                    results = fetch_speeches(
                        config,
                        day="2025-09-23",
                        slug="kenya",
                        dest=Path(tmp),
                        require_roster=True,
                    )
        scrape.assert_not_called()
        self.assertEqual(results[0].page.slug, "kenya")
        self.assertIn("roster", results[0].page.error or "")

    def test_ocr_failure_falls_back_to_audio(self) -> None:
        config = load_session("80")
        page = SpeakerPage(
            slug="lithuania",
            url="https://gadebate.un.org/en/80/lithuania",
            country="Lithuania",
            name="Gitanas Nausėda",
            rank="President",
            speaker_title="His Excellency",
            speech_date="2025-09-23",
            pdf_en=FileRef("Statement in English", "https://x/lt_en.pdf", "lt_en.pdf"),
            audio_en=FileRef("english", "https://x/80_LT_EN.mp3", "80_LT_EN.mp3", "en"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            with patch("pipeline.run.fetch", return_value=(200, {}, b"%PDF-fake")):
                with patch("pipeline.run.extract_pdf_text", return_value=""):
                    with patch(
                        "pipeline.run.ocr_pdf_text",
                        side_effect=SourceUnavailable("ocr: falta tesseract"),
                    ):
                        with patch(
                            "pipeline.run.transcribe_audio_file",
                            return_value="Madam President, " * 20,
                        ):
                            speech = extract_from_page(config, page, dest=Path(tmp))
        self.assertEqual(speech.source, "audio_en")
        self.assertEqual(speech.transformation, "whisper")

    def test_waf_pdf_falls_back_to_audio(self) -> None:
        from dataclasses import replace

        from pipeline.http import HttpError

        page = SpeakerPage(
            slug="angola",
            url="https://gadebate.un.org/en/80/angola",
            country="Angola",
            name="João Manuel Gonçalves Lourenço",
            rank="President",
            speaker_title="His Excellency",
            speech_date="2025-09-23",
            pdf_en=FileRef(
                "Statement in English",
                "https://gadebate.un.org/sites/default/files/gastatements/80/ao_en.pdf",
                "ao_en.pdf",
            ),
            audio_en=FileRef(
                "english",
                "https://s3.amazonaws.com/example/80_AO_EN.mp3",
                "80_AO_EN.mp3",
                "en",
            ),
        )

        def fake_fetch(url: str, **_kwargs):
            if url.endswith(".pdf"):
                raise HttpError(url, 202, "WAF challenge HTTP 202 (2451 bytes)")
            return (200, {}, b"fake-mp3")

        with tempfile.TemporaryDirectory() as tmp:
            config = replace(load_session("80"), root=Path(tmp))
            with patch("pipeline.run.fetch", side_effect=fake_fetch):
                with patch(
                    "pipeline.run.transcribe_audio_file",
                    return_value="Madam President, " * 20,
                ):
                    speech, via, _elapsed = extract_from_page_timed(
                        config, page, dest=Path(tmp) / "out"
                    )
        self.assertEqual(speech.source, "audio_en")
        self.assertEqual(speech.transformation, "whisper")
        self.assertEqual(via, "whisper")

    def test_waf_html_cached_as_pdf_is_ignored(self) -> None:
        from dataclasses import replace

        from pipeline.http import HttpError
        from pipeline.run import _cache_path

        config = load_session("80")
        page = SpeakerPage(
            slug="angola",
            url="https://gadebate.un.org/en/80/angola",
            country="Angola",
            name="João Manuel Gonçalves Lourenço",
            rank="President",
            speaker_title="His Excellency",
            speech_date="2025-09-23",
            pdf_en=FileRef("en", "https://gadebate.un.org/ao_en.pdf", "ao_en.pdf"),
            audio_en=FileRef("english", "https://s3.example/80_AO_EN.mp3", "80_AO_EN.mp3", "en"),
        )
        waf = (
            b"<!DOCTYPE html><html><script>window.awsWafCookieDomainList=[];"
            b"window.gokuProps={};</script></html>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            config = replace(config, root=Path(tmp))
            poisoned = _cache_path(config, "ao_en.pdf")
            poisoned.write_bytes(waf)

            def fake_fetch(url: str, **_kwargs):
                if url.endswith(".pdf"):
                    raise HttpError(url, 202, "WAF challenge HTTP 202")
                return (200, {}, b"fake-mp3")

            with patch("pipeline.run.fetch", side_effect=fake_fetch):
                with patch(
                    "pipeline.run.transcribe_audio_file",
                    return_value="Madam President, " * 20,
                ):
                    speech = extract_from_page(config, page, dest=Path(tmp) / "out")
        self.assertEqual(speech.source, "audio_en")


class OcrTest(unittest.TestCase):
    def test_iso_to_tesseract(self) -> None:
        self.assertEqual(tesseract_lang_for("en"), "eng")
        self.assertEqual(tesseract_lang_for("es"), "spa")
        self.assertEqual(tesseract_lang_for("und"), "eng")

    def test_ocr_reads_cache_without_tesseract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "lt_en.ocr.txt"
            cache.write_text("Madam President, Lithuania speaks.\n", encoding="utf-8")
            text = ocr_pdf_text(b"", cache_path=cache, label="lt_en.pdf")
        self.assertIn("Lithuania speaks", text)


if __name__ == "__main__":
    unittest.main()
