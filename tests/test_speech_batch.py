from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.detector import DetectionEvent
from app.notifier import build_speech_email
from app.speaker import Speaker
from app.speech_batch import SpeakerBatch
from app.speech_mail import send_saved
from app.speech_store import SpeechStore, country_slug


def _event(
    keyword: str,
    *,
    seconds: int,
    video_seconds: float,
    context: str | None = None,
) -> DetectionEvent:
    return DetectionEvent(
        timestamp=datetime(2026, 9, 23, 16, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds),
        keyword=keyword,
        transcript="speech",
        context=context or f'"...{keyword}..."',
        video_seconds=video_seconds,
        watch_url="https://www.youtube.com/watch?v=abc",
    )


class _Sent:
    def __init__(
        self,
        events: list[DetectionEvent],
        name: str,
        country: str | None,
        title: str | None,
    ) -> None:
        self.events = events
        self.name = name
        self.country = country
        self.title = title


class _Recorder:
    def __init__(self) -> None:
        self.speeches: list[_Sent] = []
        self.emails_sent = 0

    def send_speech(
        self,
        events: list[DetectionEvent],
        *,
        name: str,
        country: str | None,
        title: str | None,
    ) -> None:
        self.speeches.append(_Sent(list(events), name, country, title))
        self.emails_sent += 1
        return True

    def notify_status(self, subject: str, body: str) -> bool:
        return False


class BuildSpeechEmailTest(unittest.TestCase):
    def test_subject_and_quotes_follow_time_not_keyword(self) -> None:
        events = [
            _event("women", seconds=80, video_seconds=80, context='"later women"'),
            _event("gender", seconds=10, video_seconds=10, context='"first gender"'),
            _event("women", seconds=40, video_seconds=40, context='"middle women"'),
        ]
        subject, body, html = build_speech_email(
            events,
            name="Mohamed Younis Menfi",
            country="Libya",
            title="Presidential Council of the State of Libya",
        )
        self.assertEqual(subject, "gender, women | Mohamed Younis Menfi | Libya")
        self.assertIn("Country: Libya", body)
        self.assertIn("Title: Presidential Council of the State of Libya", body)
        self.assertLess(body.index("first **gender**"), body.index("middle **women**"))
        self.assertLess(body.index("middle **women**"), body.index("later **women**"))
        self.assertLess(body.index("| gender"), body.index("| women"))
        self.assertIn("Madrid:", body)
        self.assertIn("<strong>gender</strong>", html)
        self.assertNotIn("KEYWORD_DETECTED", subject)
        self.assertNotIn("Player", body)
        self.assertNotIn("UN Web TV", body)

    def test_omits_country_when_missing(self) -> None:
        subject, body, _html = build_speech_email(
            [_event("women", seconds=1, video_seconds=1)],
            name="Lula",
            country=None,
            title=None,
        )
        self.assertEqual(subject, "women | Lula")
        self.assertNotIn("Country:", body)


class SpeakerBatchTest(unittest.TestCase):
    def test_sends_when_speaker_changes_and_keeps_the_rest_on_shutdown(self) -> None:
        recorder = _Recorder()
        batch = SpeakerBatch(recorder)
        menfi = Speaker(name="Mohamed Younis Menfi", country="Libya", title="President")
        batch.note_speaker(menfi)
        batch.notify([_event("gender", seconds=10, video_seconds=10)])
        batch.notify([_event("women", seconds=40, video_seconds=40)])
        self.assertEqual(recorder.speeches, [])

        batch.note_speaker(Speaker(name="Luiz Inacio Lula da Silva", country="Brazil"))
        self.assertEqual(len(recorder.speeches), 1)
        sent = recorder.speeches[0]
        self.assertEqual(sent.name, "Mohamed Younis Menfi")
        self.assertEqual(sent.country, "Libya")
        self.assertEqual(sent.title, "President")
        self.assertEqual([event.keyword for event in sent.events], ["gender", "women"])

        batch.notify([_event("multilateralism", seconds=90, video_seconds=90)])
        batch.reset()
        batch.flush()
        self.assertEqual(len(recorder.speeches), 1)
        pending = batch.store.list_speeches()
        self.assertEqual(pending[0].name, "Luiz Inacio Lula da Silva")
        self.assertEqual([event.keyword for event in pending[0].quotes], ["multilateralism"])

    def test_same_speaker_does_not_send(self) -> None:
        recorder = _Recorder()
        batch = SpeakerBatch(recorder)
        batch.note_speaker(Speaker(name="Lula", country="Brazil"))
        batch.notify([_event("women", seconds=1, video_seconds=1)])
        batch.note_speaker(Speaker(name="Lula", title="President"))
        batch.flush()
        self.assertEqual(recorder.speeches, [])
        saved = batch.store.list_speeches()
        self.assertEqual(saved[0].title, "President")
        self.assertEqual(saved[0].country, "Brazil")

    def test_restart_keeps_quotes_until_the_next_speaker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = _Recorder()
            first = SpeakerBatch(recorder, log_dir=tmp)
            first.note_speaker(Speaker(name="Mohamed Younis Menfi", country="Libya"))
            first.notify([_event("gender", seconds=10, video_seconds=10)])
            first.flush()
            self.assertEqual(recorder.speeches, [])
            self.assertTrue((Path(tmp) / "speeches.json").is_file())

            second = SpeakerBatch(recorder, log_dir=tmp)
            second.note_speaker(Speaker(name="Mohamed Younis Menfi", country="Libya"))
            second.notify([_event("women", seconds=40, video_seconds=40)])
            second.note_speaker(Speaker(name="Lula", country="Brazil"))
            self.assertEqual(len(recorder.speeches), 1)
            self.assertEqual(
                [event.keyword for event in recorder.speeches[0].events],
                ["gender", "women"],
            )


class SpeechStoreTest(unittest.TestCase):
    def test_country_slug(self) -> None:
        self.assertEqual(country_slug("Libya"), "libya")
        self.assertEqual(country_slug("Iran (Islamic Republic of)"), "iran")
        self.assertEqual(country_slug("Syrian Arab Republic"), "syrian-arab-republic")
        self.assertEqual(country_slug("Côte d'Ivoire"), "cote-d-ivoire")
        self.assertEqual(country_slug("Timor-Leste"), "timor-leste")

    def test_manual_send_by_slug_or_partial_name(self) -> None:
        recorder = _Recorder()
        store = SpeechStore("")
        store.add(
            Speaker(name="Mohamed Younis Menfi", country="Libya", title="President"),
            [_event("gender", seconds=10, video_seconds=10)],
        )
        self.assertEqual(send_saved(store, recorder, country="libya"), "ok")
        self.assertEqual(recorder.speeches[0].name, "Mohamed Younis Menfi")
        self.assertEqual(store.list_speeches(), [])

        store.add(
            Speaker(name="Mohamed Younis Menfi", country="Libya"),
            [_event("women", seconds=20, video_seconds=20)],
        )
        self.assertEqual(send_saved(store, recorder, name="Menfi"), "ok")
        self.assertEqual(store.list_speeches(), [])

    def test_ambiguous_country_does_not_send(self) -> None:
        recorder = _Recorder()
        store = SpeechStore("")
        store.add(Speaker(name="A", country="Libya"), [_event("gender", seconds=1, video_seconds=1)])
        store.add(Speaker(name="B", country="Libya"), [_event("women", seconds=2, video_seconds=2)])
        self.assertEqual(send_saved(store, recorder, country="Libya"), "ambiguous")
        self.assertEqual(recorder.speeches, [])
        self.assertEqual(len(store.list_speeches()), 2)

    def test_failed_send_puts_quotes_back(self) -> None:
        class _Down:
            emails_sent = 0

            def send_speech(self, events, *, name, country, title) -> bool:
                return False

            def notify_status(self, subject: str, body: str) -> bool:
                return False

        store = SpeechStore("")
        store.add(Speaker(name="Lula", country="Brazil"), [_event("women", seconds=1, video_seconds=1)])
        self.assertEqual(send_saved(store, _Down(), name="Lula"), "failed")
        self.assertEqual(len(store.list_speeches()), 1)
        self.assertEqual(store.list_speeches()[0].quotes[0].keyword, "women")


if __name__ == "__main__":
    unittest.main()
