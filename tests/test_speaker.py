from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
        log_dir="",
        cookies_file=None,
        audio_read_timeout=30,
        smtp_host="",
        smtp_port=587,
        smtp_user="",
        smtp_password="",
        smtp_from="",
        smtp_user_b="",
        smtp_password_b="",
        smtp_from_b="",
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
        self.assertIsNotNone(speaker.country)
        assert speaker.country is not None
        self.assertIn("Brazil", speaker.country)
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
        tracker.observe("I now give the floor to His Excellency Lula da Silver, President of Brazil.")
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
        # Segundo chunk sigue siendo intro (completa el nombre): no atribuir aún.
        mid = tracker.observe("Lula da Silva, President of the Federative Republic of Brazil.")
        self.assertEqual(mid.name, "unknown")
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

    def test_president_of_brasil_maps_roster_country(self) -> None:
        tracker = SpeakerTracker(
            _config(
                speaker_roster=(
                    "Luiz Inacio Lula da Silva | Brazil | President of the Federative Republic of Brazil;"
                    "Emmanuel Macron | France | President of the French Republic"
                )
            )
        )
        first = tracker.observe("I now give the floor to the President of Brasil.")
        self.assertEqual(first.name, "unknown")
        speaker = tracker.observe("Brazil remains committed to multilateralism.")
        self.assertEqual(speaker.name, "Luiz Inacio Lula da Silva")
        self.assertEqual(speaker.country, "Brasil")

    def test_mid_speech_country_mention_does_not_switch(self) -> None:
        tracker = SpeakerTracker(
            _config(
                speaker_roster=(
                    "Luiz Inacio Lula da Silva | Brazil | President;"
                    "Emmanuel Macron | France | President"
                )
            )
        )
        tracker.observe(LULA_INTRO)
        tracker.observe("The speech begins.")
        later = tracker.observe("We thank the president of France for the climate pledge.")
        self.assertIn("Lula", later.name)

    def test_honorifics_extract_majesty_and_royal_highness(self) -> None:
        majesty = extract_introduction_regex(
            "I now give the floor to His Majesty Abdullah II ibn Al Hussein, King of Jordan."
        )
        assert majesty is not None
        self.assertIn("Abdullah", majesty.name)
        self.assertTrue(has_introduction_cue("I now give the floor to His Majesty Abdullah II"))
        self.assertFalse(has_introduction_cue("His Majesty Abdullah II"))

        highness = extract_introduction_regex(
            "The Assembly will hear an address by His Highness Sheikh Tamim bin Hamad "
            "Al Thani, Amir of Qatar."
        )
        assert highness is not None
        self.assertIn("Tamim", highness.name)

        royal = extract_introduction_regex(
            "I give the floor to His Royal Highness Guillaume of Luxembourg, Grand Duke."
        )
        assert royal is not None
        self.assertIn("Guillaume", royal.name)
        self.assertTrue(has_introduction_cue("The Assembly will hear an address by Her Royal Highness"))
        self.assertFalse(has_introduction_cue("Her Royal Highness"))

    def test_agenda_only_ignores_excellency_without_country(self) -> None:
        tracker = SpeakerTracker(
            _config(
                speaker_roster=(
                    "Luiz Inacio Lula da Silva | Brazil | President;"
                    "Emmanuel Macron | France | President"
                )
            )
        )
        first = tracker.observe("I now give the floor to His Excellency Gabriel Boric.")
        self.assertEqual(first.name, "unknown")
        self.assertEqual(tracker.observe("Chile remains committed.").name, "unknown")

        tracker.observe(LULA_INTRO)
        tracker.observe("The speech begins.")
        later = tracker.observe(
            "I now give the floor to Her Excellency Claudia Sheinbaum Pardo."
        )
        self.assertIn("Lula", later.name)
        self.assertIn("Lula", tracker.observe("We continue.").name)

    def test_full_intro_not_in_agenda_is_unverified(self) -> None:
        tracker = SpeakerTracker(
            _config(
                speaker_roster=(
                    "Luiz Inacio Lula da Silva | Brazil | President;"
                    "Emmanuel Macron | France | President"
                )
            )
        )
        tracker.observe(LULA_INTRO)
        tracker.observe("The speech begins.")
        intro = tracker.observe(
            "I now give the floor to His Excellency Gabriel Boric, President of Chile."
        )
        self.assertEqual(intro.name, "unknown")
        speaker = tracker.observe("Chile remains committed.")
        self.assertEqual(speaker.name, "Gabriel Boric")
        self.assertEqual(speaker.source, "asr-unverified")

    def test_pga_mention_is_not_unverified_speaker(self) -> None:
        tracker = SpeakerTracker(
            _config(speaker_roster="Luiz Inacio Lula da Silva | Brazil | President")
        )
        tracker.observe(LULA_INTRO)
        tracker.observe("The speech begins.")
        tracker.observe(
            "I give the floor to His Excellency Khalilur Rahman, President of the General Assembly."
        )
        self.assertIn("Lula", tracker.observe("We continue.").name)

    def test_agenda_majesty_maps_roster(self) -> None:
        tracker = SpeakerTracker(
            _config(
                speaker_roster=(
                    "Abdullah II ibn Al Hussein | Jordan | King;"
                    "Luiz Inacio Lula da Silva | Brazil | President"
                )
            )
        )
        tracker.observe(
            "The Assembly will hear an address by His Majesty Abdullah II ibn Al Hussein, "
            "King of Jordan."
        )
        speaker = tracker.observe("Jordan remains committed to peace.")
        self.assertEqual(speaker.name, "Abdullah II ibn Al Hussein")

    def test_congratulating_pga_does_not_switch_to_trump(self) -> None:
        roster = (
            "João Manuel Gonçalves Lourenço | Angola | President of the Republic of Angola;"
            "Donald Trump | United States of America | President of the United States of America;"
            "Dr. Khalilur Rahman | President of the General Assembly (opening) | President of the General Assembly;"
            "António Guterres | Secretary-General of the United Nations | Secretary-General;"
            "Andrew Burnham | United Kingdom of Great Britain and Northern Ireland | Prime Minister;"
            "Deogratius Ndejembi | United Republic of Tanzania | Vice-President"
        )
        tracker = SpeakerTracker(_config(speaker_roster=roster))
        tracker.observe(
            "The Assembly will hear an address by His Excellency João Manuel Gonçalves Lourenço, "
            "President of the Republic of Angola. I request protocol to escort his excellency "
            "and invite him to address the assembly."
        )
        speaker = tracker.observe("Ro Renzo, President of the Republic of Angola.")
        self.assertEqual(speaker.name, "João Manuel Gonçalves Lourenço")
        later = tracker.observe(
            "Gentlemen, allow me to congratulate his Excellency Khalilur Rahman on his election "
            "as president of the Erie First Session of the United Nations General Assembly"
        )
        self.assertEqual(later.name, "João Manuel Gonçalves Lourenço")
        self.assertEqual(tracker.current.name, "João Manuel Gonçalves Lourenço")
        still = tracker.observe(
            "this year's session, however, takes place 25 years after the terro"
        )
        self.assertEqual(still.name, "João Manuel Gonçalves Lourenço")

    def test_whisper_by_title_keeps_country_and_maps_senegal(self) -> None:
        text = (
            "The assembly will now hear an address from his Excellency, "
            "Mr. Basirou Diyama, Jaka, by President of the Republic of Senegal."
        )
        speaker = extract_introduction_regex(text)
        assert speaker is not None
        self.assertIn("Basirou", speaker.name)
        self.assertNotIn("President", speaker.name)
        self.assertIn("Senegal", speaker.country or "")

        tracker = SpeakerTracker(
            _config(
                speaker_roster=(
                    "Luís Montenegro | Portugal | Prime Minister;"
                    "Bassirou Diomaye Diakhar Faye | Senegal | President;"
                    "Benjamin Netanyahu | Israel | Prime Minister"
                )
            )
        )
        intro = tracker.observe(text)
        self.assertEqual(intro.name, "unknown")
        speech = tracker.observe("President, on behalf of Senegal, I thank the Assembly.")
        self.assertEqual(speech.name, "Bassirou Diomaye Diakhar Faye")

    def test_next_sentence_country_confirms_garbled_name(self) -> None:
        tracker = SpeakerTracker(
            _config(
                speaker_roster=(
                    "Luís Montenegro | Portugal | Prime Minister;"
                    "Bassirou Diomaye Diakhar Faye | Senegal | President;"
                    "Benjamin Netanyahu | Israel | Prime Minister"
                )
            )
        )
        tracker.observe(
            "The assembly will now hear an address from his Excellency "
            "Mr. Basirou Diyama Jaka."
        )
        speech = tracker.observe("President, on behalf of Senegal, I thank the Assembly.")
        self.assertEqual(speech.name, "Bassirou Diomaye Diakhar Faye")
        later = tracker.observe("We also thank the people of Portugal for their support.")
        self.assertEqual(later.name, "Bassirou Diomaye Diakhar Faye")

    def test_next_sentence_country_does_not_confirm_unrelated_name(self) -> None:
        tracker = SpeakerTracker(
            _config(
                speaker_roster=(
                    "Bassirou Diomaye Diakhar Faye | Senegal | President;"
                    "Benjamin Netanyahu | Israel | Prime Minister"
                )
            )
        )
        tracker.observe(
            "I now give the floor to His Excellency John Smith, and invite him to address the assembly."
        )
        speech = tracker.observe("We condemn the war in Israel and stand with Senegal.")
        self.assertEqual(speech.name, "unknown")

    def test_glued_prime_minister_of_israel_maps_netanyahu(self) -> None:
        tracker = SpeakerTracker(
            _config(
                speaker_roster=(
                    "Benjamin Netanyahu | Israel | Prime Minister;"
                    "Luís Montenegro | Portugal | Prime Minister"
                )
            )
        )
        tracker.observe(
            "The assembly will now hear an address from his Excellency "
            "Mr. Benyamin Netanyahu, by Prime Minister of the State of Israel."
        )
        speech = tracker.observe("Mr. President, Israel will defend itself.")
        self.assertEqual(speech.name, "Benjamin Netanyahu")


class SpeakerPersistenceTest(unittest.TestCase):
    def test_restart_restores_speaker_from_the_same_day(self) -> None:
        now = datetime(2026, 9, 23, 18, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            tracker = SpeakerTracker(_config(log_dir=tmp), now=lambda: now)
            tracker.observe(LULA_INTRO, video_seconds=100)
            speaker = tracker.observe("We must protect women.")
            self.assertIn("Lula", speaker.name)

            again = SpeakerTracker(_config(log_dir=tmp), now=lambda: now)
            self.assertIn("Lula", again.current.name)
            self.assertEqual(again.observe("The speech continues.").name, again.current.name)

    def test_discards_speaker_older_than_90_minutes(self) -> None:
        now = datetime(2026, 9, 23, 18, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "speaker.json"
            started = now - timedelta(minutes=91)
            path.write_text(
                json.dumps(
                    {
                        "current": {
                            "name": "Luiz Inacio Lula da Silva",
                            "title": "President",
                            "country": "Brazil",
                            "confidence": "strong",
                            "source": "regex",
                            "started_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        }
                    }
                ),
                encoding="utf-8",
            )
            tracker = SpeakerTracker(_config(log_dir=tmp), now=lambda: now)
            self.assertEqual(tracker.current.name, "unknown")
            self.assertFalse(path.is_file())

    def test_discards_speaker_from_the_previous_new_york_day(self) -> None:
        # 00:20 en Nueva York; el orador empezó 23:40 del día anterior (40 min).
        now = datetime(2026, 9, 23, 4, 20, tzinfo=timezone.utc)
        started = now - timedelta(minutes=40)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "speaker.json"
            path.write_text(
                json.dumps(
                    {
                        "current": {
                            "name": "Luiz Inacio Lula da Silva",
                            "title": "President",
                            "country": "Brazil",
                            "confidence": "strong",
                            "source": "regex",
                            "started_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        }
                    }
                ),
                encoding="utf-8",
            )
            tracker = SpeakerTracker(_config(log_dir=tmp), now=lambda: now)
            self.assertEqual(tracker.current.name, "unknown")

    def test_reset_forgets_saved_speaker(self) -> None:
        now = datetime(2026, 9, 23, 18, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            tracker = SpeakerTracker(_config(log_dir=tmp), now=lambda: now)
            tracker.observe(LULA_INTRO)
            tracker.observe("Speech starts.")
            tracker.reset()
            again = SpeakerTracker(_config(log_dir=tmp), now=lambda: now)
            self.assertEqual(again.current.name, "unknown")


# Intros reales del 25/09/2026 (Whisper base) y la de Sudáfrica del 24/09.
KIRIBATI_INTRO = (
    "The Assembly will hear an address by his excellency, tenety, mama, "
    "president and minister for of the Republic of Kiribati."
)
MALDIVES_INTRO = (
    "The Assembly will hear an address by his excellency Hussain Muhammad Latif, "
    "Vice President of the Republic of Maltives. I request Protocol to escort his "
    "excellency and invite him to address the Assembly."
)
IRAQ_INTRO = (
    "I wish to thank the Vice President of the Republic of Maltes. The Assembly will now "
    "hear an address by his excellency Ali Fali al-Zahidi, Prime Minister of the Republic "
    "of Iraq. I request protocol to escort his excellency and invite him to address the Assembly."
)
LEBANON_INTRO = (
    "The Assembly will hear an address by his Excellency Nawaf Salam, President of the "
    "Council of Ministers of the Lebanese Republic. I request Protocol to escort his "
    "Excellency and invite him to address the Assembly."
)
LAMOLA_INTRO = (
    "I now give the floor to his Excellency Ronald Aziz-Lomola, Minister for International "
    "Relations and Cooperation of South Africa."
)

DAY_ROSTER = {
    "session": 81,
    "day": "2026-09-25",
    "speakers": [
        {"country": "Kiribati", "name": "Taneti Maamau", "rank": "", "speaker_title": ""},
        {"country": "Maldives", "name": "Hussain Mohamed Latheef", "rank": "", "speaker_title": ""},
        {"country": "Iraq", "name": "Ali Falih Al-Zaidi", "rank": "", "speaker_title": ""},
        {"country": "Lebanon", "name": "Nawaf Salam", "rank": "", "speaker_title": ""},
        {"country": "Monaco", "name": "Christophe Mirmand", "rank": "", "speaker_title": ""},
        {"country": "South Africa", "name": "Ronald Ozzy Lamola", "rank": "", "speaker_title": ""},
    ],
}
STALE_TXT = "Balendra Shah | Nepal\nHilda Heine | Marshall Islands\n"
TODAY = datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc)


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class DayRosterTest(unittest.TestCase):
    def _dirs(self, tmp: str, *, with_json: bool) -> tuple[Path, Path]:
        roster_dir = Path(tmp) / "roster"
        roster_dir.mkdir()
        if with_json:
            (roster_dir / "2026-09-25.json").write_text(json.dumps(DAY_ROSTER), encoding="utf-8")
        speakers_txt = Path(tmp) / "speakers.txt"
        speakers_txt.write_text(STALE_TXT, encoding="utf-8")
        return roster_dir, speakers_txt

    def test_day_json_maps_todays_real_intros(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            roster_dir, speakers_txt = self._dirs(tmp, with_json=True)
            tracker = SpeakerTracker(
                _config(speaker_roster_dir=str(roster_dir), speaker_roster_file=str(speakers_txt)),
                now=lambda: TODAY,
            )
            names = [entry.name for entry in tracker.roster]
            self.assertNotIn("Balendra Shah", names)
            expected = [
                (KIRIBATI_INTRO, "Taneti Maamau"),
                (MALDIVES_INTRO, "Hussain Mohamed Latheef"),
                (IRAQ_INTRO, "Ali Falih Al-Zaidi"),
                (LEBANON_INTRO, "Nawaf Salam"),
            ]
            for intro, name in expected:
                tracker.observe(intro)
                speaker = tracker.observe("Mr. President, Secretary-General, excellencies.")
                self.assertEqual(speaker.name, name)
                self.assertNotEqual(speaker.source, "asr-unverified")

    def test_missing_day_json_falls_back_warns_once_and_reloads(self) -> None:
        calls: list[tuple[str, str]] = []
        clock = _Clock()
        with tempfile.TemporaryDirectory() as tmp:
            roster_dir, speakers_txt = self._dirs(tmp, with_json=False)
            tracker = SpeakerTracker(
                _config(speaker_roster_dir=str(roster_dir), speaker_roster_file=str(speakers_txt)),
                now=lambda: TODAY,
                on_roster_stale=lambda subject, body: calls.append((subject, body)),
                monotonic=clock,
            )
            self.assertEqual([entry.name for entry in tracker.roster], ["Balendra Shah", "Hilda Heine"])
            self.assertEqual(len(calls), 1)
            self.assertIn("2026-09-25", calls[0][0])
            tracker.refresh_roster(force=True)
            self.assertEqual(len(calls), 1)

            (roster_dir / "2026-09-25.json").write_text(json.dumps(DAY_ROSTER), encoding="utf-8")
            clock.value = 30.0
            self.assertFalse(tracker.refresh_roster())
            clock.value = 61.0
            tracker.observe(IRAQ_INTRO)
            self.assertEqual(tracker.observe("Mr. President.").name, "Ali Falih Al-Zaidi")

    def test_stale_roster_still_switches_on_full_intro(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            roster_dir, speakers_txt = self._dirs(tmp, with_json=False)
            tracker = SpeakerTracker(
                _config(speaker_roster_dir=str(roster_dir), speaker_roster_file=str(speakers_txt)),
                now=lambda: TODAY,
            )
            tracker.observe(
                "The assembly will hear an address by his Excellency Balendra Shah, "
                "Prime Minister of Nepal."
            )
            self.assertEqual(tracker.observe("Mr. President.").name, "Balendra Shah")
            tracker.observe(LAMOLA_INTRO)
            speaker = tracker.observe("South Africa remains committed to multilateralism.")
            self.assertEqual(speaker.name, "Ronald Aziz-Lomola")
            self.assertEqual(speaker.source, "asr-unverified")
            tracker.observe(LEBANON_INTRO)
            self.assertEqual(tracker.observe("Mr. President.").name, "Nawaf Salam")

    def test_lamola_maps_to_roster_when_listed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            roster_dir, speakers_txt = self._dirs(tmp, with_json=True)
            tracker = SpeakerTracker(
                _config(speaker_roster_dir=str(roster_dir), speaker_roster_file=str(speakers_txt)),
                now=lambda: TODAY,
            )
            tracker.observe(LAMOLA_INTRO)
            self.assertEqual(tracker.observe("Mr. President.").name, "Ronald Ozzy Lamola")


EVENING_ROSTER = {
    "session": 81,
    "day": "2026-09-25",
    "speakers": [
        {"country": "Guatemala", "name": "Carlos Ramiro Martínez Alvarado", "rank": "", "speaker_title": ""},
        {"country": "Sweden", "name": "Maria Malmer Stenergard", "rank": "", "speaker_title": ""},
        {"country": "Togo", "name": "Robert Komlan Edo Dussey", "rank": "", "speaker_title": ""},
    ],
}
GUATEMALA_INTRO = (
    "And I'll give the floor to his excellency Carlos Ramero Martinez Alvarado, "
    "Minister of Foreign Affairs of Guatemala."
)
TOGO_INTRO = (
    "I thank the Minister of Foreign Affairs of Sweden. I now give the floor to his excellency "
    "Robert Comlan Edo Ducey, Minister of Foreign Affairs, Regina Integrations"
)


class SpeechEndTest(unittest.TestCase):
    """Secuencia real del 25/9 a la noche (Guatemala → Suecia → Togo → réplicas → cierre)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        roster_dir = Path(self._tmp.name) / "roster"
        roster_dir.mkdir()
        (roster_dir / "2026-09-25.json").write_text(json.dumps(EVENING_ROSTER), encoding="utf-8")
        self.now = datetime(2026, 9, 26, 0, 46, tzinfo=timezone.utc)
        self.tracker = SpeakerTracker(
            _config(speaker_roster_dir=str(roster_dir)),
            now=lambda: self.now,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _say(self, text: str, *, after: float = 30.0) -> str:
        self.now += timedelta(seconds=after)
        return self.tracker.observe(text).name

    def _start_guatemala(self) -> None:
        self._say(GUATEMALA_INTRO)
        self.assertEqual(self._say("Mr. President."), "Carlos Ramiro Martínez Alvarado")

    def test_intro_without_her_after_chair_thanks(self) -> None:
        self._start_guatemala()
        closing = (
            "that none of us could achieve alone. Thank you very much. "
            "I thank the Minister of Foreign Affairs of Guatemala. I now"
        )
        self.assertEqual(self._say(closing, after=900), "Carlos Ramiro Martínez Alvarado")
        self._say(
            "We are Excellency Maria Malma Stena-Gad, Minister of Foreign Affairs, or Sweden. "
            "Mr. President, Excellencies, I want to begin"
        )
        self.assertEqual(self._say("Growing up in rural Sweden."), "Maria Malmer Stenergard")

    def test_last_speaker_replies_and_adjournment(self) -> None:
        self._start_guatemala()
        self._say(TOGO_INTRO, after=900)
        self.assertEqual(self._say("Togo leads abroad of Togo."), "Robert Komlan Edo Dussey")
        closing = (
            "Thank you for your kind attention. I thank the Minister of Foreign Affairs, "
            "Regional Integration and Togolese Abroad of Togo."
        )
        self.assertEqual(self._say(closing, after=1200), "Robert Komlan Edo Dussey")
        self.assertEqual(
            self._say("the last speaker in the general debate for this meeting will continue tomorrow"),
            "unknown",
        )
        self._say("I call on the representative of India. Thank you, Mr. President.")
        self.assertEqual(self._say("This context, Mr. President."), "India (right of reply)")
        self._say("I call on the representative of Pakistan. Thank you, Mr. President.")
        self.assertEqual(self._say("Hindutva ideology."), "Pakistan (right of reply)")
        self._say("Can fight but in peace. The meeting is adjourned.")
        self.assertEqual(self._say("half are women and girls."), "unknown")

    def test_meeting_end_alone_closes_speaker(self) -> None:
        self._start_guatemala()
        self._say("The meeting is adjourned.", after=600)
        self.assertEqual(self._say("women and girls"), "unknown")

    def test_thanking_own_president_mid_speech_does_not_close(self) -> None:
        self._start_guatemala()
        self._say("I thank the President of Guatemala, Bernardo Arévalo, for his leadership.", after=300)
        self.assertEqual(self._say("women"), "Carlos Ramiro Martínez Alvarado")

    def test_thanks_right_after_intro_does_not_close(self) -> None:
        self._start_guatemala()
        self._say("Thank you. I thank the people of Guatemala.")
        self.assertEqual(self._say("women"), "Carlos Ramiro Martínez Alvarado")

    def test_your_excellency_in_speech_is_not_an_intro(self) -> None:
        speaker = extract_introduction_regex(
            "I now give the floor to His Excellency Carlos Ramero Martinez Alvarado, Minister of "
            "Foreign Affairs of Guatemala. Your Excellency Secretary-General Antonio Guterres,"
        )
        assert speaker is not None
        self.assertEqual(speaker.name, "Carlos Ramero Martinez Alvarado")


if __name__ == "__main__":
    unittest.main()
