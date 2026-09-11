from __future__ import annotations

import unittest
from typing import Any

from app.config import Config
from app.speaker import SpeakerTracker, extract_introduction_regex, has_introduction_cue


LULA_INTRO = (
    "I now give the floor to His Excellency Luiz Inacio Lula da Silva, "
    "President of the Federative Republic of Brazil."
)


def _config(**overrides) -> Config:
    values = dict(
        stream_url="https://www.youtube.com/watch?v=KnIFmbdRCi0",
        keywords=("women",),
        whisper_model="base",
        whisper_device="cpu",
        whisper_compute_type="int8",
        whisper_beam_size=1,
        whisper_vad=True,
        language="en",
        chunk_seconds=20,
        chunk_overlap_seconds=2,
        context_words=20,
        reconnect_delay=10,
        heartbeat_seconds=60,
        cpu_threads=4,
        log_level="INFO",
        cookies_file=None,
        audio_read_timeout=30,
        smtp_host="",
        smtp_port=587,
        smtp_user="",
        smtp_password="",
        smtp_from="",
        alert_email_to=(),
        smtp_starttls=True,
        smtp_ssl=False,
        alert_cooldown_seconds=120,
        smtp_timeout=15,
        speaker_tracking=True,
        speaker_aliases="",
        speaker_llm_api_key="",
    )
    values.update(overrides)
    return Config(**values)


class IntroductionRegexTest(unittest.TestCase):
    def test_extracts_un_style_name_and_title(self) -> None:
        speaker = extract_introduction_regex(LULA_INTRO)
        assert speaker is not None
        self.assertEqual(speaker.confidence, "strong")
        self.assertIn("Lula", speaker.name)
        self.assertIn("Inacio", speaker.name)
        self.assertIsNotNone(speaker.title)
        assert speaker.title is not None
        self.assertIn("Brazil", speaker.title)

    def test_her_excellency_extracts_name(self) -> None:
        speaker = extract_introduction_regex(
            "I now give the floor to Her Excellency Claudia Sheinbaum Pardo, "
            "President of Mexico, and invite her to address the assembly."
        )
        assert speaker is not None
        self.assertEqual(speaker.confidence, "strong")
        self.assertIn("Sheinbaum", speaker.name)
        assert speaker.title is not None
        self.assertIn("Mexico", speaker.title)
        self.assertNotIn("invite", speaker.title.casefold())
        text = (
            "the Photo Library of the United Nations. By this, the Assembly will hear "
            "and address by his excellency, Luis Narsio Lula da Silva, President of the "
            "Federative Republic of Brazil, and invite him to address the assembly."
        )
        speaker = extract_introduction_regex(text)
        assert speaker is not None
        self.assertEqual(speaker.confidence, "strong")
        self.assertIn("Lula", speaker.name)
        self.assertIn("Luis", speaker.name)
        self.assertNotIn("invite", speaker.name.casefold())
        assert speaker.title is not None
        self.assertIn("Brazil", speaker.title)
        self.assertNotIn("invite", speaker.title.casefold())

    def test_ignores_excellency_without_a_person_name(self) -> None:
        speaker = extract_introduction_regex(
            "I give the floor to his excellency and invite him to address the assembly."
        )
        self.assertIsNone(speaker)

    def test_weak_name_is_not_strong(self) -> None:
        speaker = extract_introduction_regex("His Excellency Lula, President of Brazil.")
        assert speaker is not None
        self.assertEqual(speaker.confidence, "weak")


class SpeakerTrackerTest(unittest.TestCase):
    def test_intro_applies_to_following_chunk(self) -> None:
        tracker = SpeakerTracker(_config())
        first = tracker.observe(LULA_INTRO, video_seconds=2845)
        self.assertEqual(first.name, "unknown")
        second = tracker.observe("We must protect women and refugees.")
        self.assertIn("Lula", second.name)
        self.assertEqual(second.source, "regex")

    def test_speaker_persists_until_next_intro(self) -> None:
        tracker = SpeakerTracker(_config())
        tracker.observe(LULA_INTRO)
        tracker.observe("First line of the speech.")
        third = tracker.observe("And we continue the same intervention.")
        self.assertIn("Lula", third.name)

    def test_weak_match_uses_llm_when_injected(self) -> None:
        calls: list[str] = []

        def fake_llm(text: str, previous: str | None) -> dict[str, Any]:
            calls.append(text)
            return {
                "is_introduction": True,
                "name": "Emmanuel Macron",
                "title": "President",
                "country": "France",
            }

        tracker = SpeakerTracker(_config(speaker_llm_api_key="sk-test"), llm_call=fake_llm)
        tracker.observe("I now give the floor to the distinguished representative of France.")
        self.assertEqual(len(calls), 1)
        speaker = tracker.observe("France stands with refugees.")
        self.assertEqual(speaker.name, "Emmanuel Macron")
        self.assertEqual(speaker.source, "llm")
        self.assertEqual(speaker.display_title, "President, France")

    def test_without_api_key_weak_intro_does_not_crash(self) -> None:
        tracker = SpeakerTracker(_config())
        speaker = tracker.observe("I now give the floor to the distinguished representative.")
        self.assertEqual(speaker.name, "unknown")
        self.assertEqual(tracker.observe("Some later remarks.").name, "unknown")

    def test_llm_false_introduction_does_not_replace_speaker(self) -> None:
        def fake_llm(text: str, previous: str | None) -> dict[str, Any]:
            return {"is_introduction": False, "name": None, "title": None, "country": None}

        tracker = SpeakerTracker(_config(speaker_llm_api_key="sk-test"), llm_call=fake_llm)
        tracker.observe(LULA_INTRO)
        tracker.observe("Speech starts.")
        tracker.observe("I thank the distinguished delegate.")
        self.assertIn("Lula", tracker.current.name)

    def test_alias_rewrites_asr_name(self) -> None:
        tracker = SpeakerTracker(
            _config(speaker_aliases="lula:Luiz Inacio Lula da Silva"),
        )
        tracker.observe("His Excellency Lula da Silver, President of Brazil.")
        speaker = tracker.observe("The speech begins.")
        self.assertEqual(speaker.name, "Luiz Inacio Lula da Silva")

    def test_roster_maps_garbled_whisper_name(self) -> None:
        tracker = SpeakerTracker(
            _config(
                speaker_roster="Luiz Inacio Lula da Silva;Emmanuel Macron",
            )
        )
        tracker.observe(
            "The Assembly will hear an address by her excellency, Lewis Nasier-Loula Dare Silva, "
            "president of Brazil, and invite her to address the assembly."
        )
        speaker = tracker.observe("Madam President, heads of state.")
        self.assertEqual(speaker.name, "Luiz Inacio Lula da Silva")

    def test_name_split_across_chunks(self) -> None:
        tracker = SpeakerTracker(_config())
        first = tracker.observe(
            "The Assembly will now hear an address by His Excellency Luiz Inacio"
        )
        self.assertEqual(first.name, "unknown")
        mid = tracker.observe("Lula da Silva, President of the Federative Republic of Brazil.")
        self.assertIn("Inacio", mid.name)
        full = tracker.observe("Brazil remains committed to multilateralism.")
        self.assertIn("Lula", full.name)
        self.assertIn("Inacio", full.name)

    def test_whisper_commas_inside_name_map_to_roster(self) -> None:
        text = (
            "The assembly will here and address by his excellency red-chap, "
            "tie-jip, Erdogan, president of Republic of Jekir. I re..."
        )
        speaker = extract_introduction_regex(text)
        assert speaker is not None
        self.assertIn("Erdogan", speaker.name)
        self.assertNotEqual(speaker.name.casefold(), "red-chap")
        assert speaker.title is not None
        self.assertIn("president", speaker.title.casefold())

        tracker = SpeakerTracker(
            _config(speaker_roster="Recep Tayyip Erdoğan;Daniel Francisco Chapo;Emmanuel Macron")
        )
        tracker.observe(text)
        self.assertEqual(tracker.observe("Turkey remains committed.").name, "Recep Tayyip Erdoğan")


if __name__ == "__main__":
    unittest.main()
