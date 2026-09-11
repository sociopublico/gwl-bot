from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.config import Config
from app.detector import DetectionEvent
from app.notifier import (
    EmailNotifier,
    NullNotifier,
    build_email,
    build_notifier,
    mark_sent,
    select_events_for_alert,
)


def _event(keyword: str, context: str = '"...context..."') -> DetectionEvent:
    return DetectionEvent(
        timestamp=datetime(2026, 9, 1, 15, 32, 17, tzinfo=timezone.utc),
        keyword=keyword,
        transcript="Today we want to talk about women and gender.",
        context=context,
    )


class SelectEventsForAlertTest(unittest.TestCase):
    def test_keeps_first_match_per_keyword(self) -> None:
        events = [_event("women", '"first"'), _event("women", '"second"'), _event("gender")]
        selected = select_events_for_alert(events, {}, cooldown_seconds=120, now=10)
        self.assertEqual([e.keyword for e in selected], ["women", "gender"])
        self.assertEqual(selected[0].context, '"first"')

    def test_skips_keyword_still_in_cooldown(self) -> None:
        last_sent = {"women": 50.0}
        events = [_event("women"), _event("gender")]
        selected = select_events_for_alert(events, last_sent, cooldown_seconds=120, now=100)
        self.assertEqual([e.keyword for e in selected], ["gender"])

    def test_sends_again_after_cooldown(self) -> None:
        last_sent = {"women": 10.0}
        events = [_event("women")]
        selected = select_events_for_alert(events, last_sent, cooldown_seconds=120, now=140)
        self.assertEqual([e.keyword for e in selected], ["women"])

    def test_cooldown_zero_always_sends(self) -> None:
        last_sent = {"women": 100.0}
        selected = select_events_for_alert([_event("women")], last_sent, cooldown_seconds=0, now=100)
        self.assertEqual(len(selected), 1)


class BuildEmailTest(unittest.TestCase):
    def test_subject_and_body_include_keywords(self) -> None:
        subject, body = build_email(
            [_event("women", '"...talk about women..."'), _event("gender", '"...and gender..."')]
        )
        self.assertEqual(subject, "KEYWORD_DETECTED | women, gender")
        self.assertIn("Keyword(s): women, gender", body)
        self.assertIn("Time: 2026-09-01 15:32:17", body)
        self.assertIn("context: \"...talk about women...\"", body)
        self.assertIn("Today we want to talk about women and gender.", body)

    def test_includes_speaker_and_watch_url(self) -> None:
        event = _event("women", '"...talk about women..."')
        event = DetectionEvent(
            timestamp=event.timestamp,
            keyword=event.keyword,
            transcript=event.transcript,
            context=event.context,
            speaker="Luiz Inacio Lula da Silva",
            speaker_title="President of Brazil",
            video_seconds=2845,
            watch_url="https://www.youtube.com/watch?v=KnIFmbdRCi0&t=2845s",
        )
        subject, body = build_email([event])
        self.assertEqual(subject, "KEYWORD_DETECTED | women | Luiz Inacio Lula da Silva")
        self.assertIn("Speaker: Luiz Inacio Lula da Silva", body)
        self.assertIn("Video time: 47:25", body)
        self.assertIn("Watch: https://www.youtube.com/watch?v=KnIFmbdRCi0&t=2845s", body)
        self.assertIn("context: \"...talk about women...\"", body)

    def test_mark_sent_uses_casefold(self) -> None:
        last_sent: dict[str, float] = {}
        mark_sent(last_sent, [_event("Women")], now=42.0)
        self.assertEqual(last_sent["women"], 42.0)


def _config(**overrides) -> Config:
    values = dict(
        stream_url="https://example.com",
        keywords=("women",),
        whisper_model="base",
        whisper_device="cpu",
        whisper_compute_type="int8",
        whisper_beam_size=1,
        whisper_vad=True,
        language="en",
        chunk_seconds=10,
        chunk_overlap_seconds=1,
        context_words=8,
        reconnect_delay=10,
        heartbeat_seconds=60,
        cpu_threads=4,
        log_level="INFO",
        cookies_file=None,
        audio_read_timeout=30,
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="user",
        smtp_password="secret",
        smtp_from="bot@example.com",
        alert_email_to=("alerts@example.com",),
        smtp_starttls=True,
        smtp_ssl=False,
        alert_cooldown_seconds=120,
        smtp_timeout=15,
    )
    values.update(overrides)
    return Config(**values)


class EmailNotifierTest(unittest.TestCase):
    def test_sends_one_email_for_chunk_and_respects_cooldown(self) -> None:
        sent: list[tuple[str, str]] = []
        clock = {"now": 10.0}

        class Recording(EmailNotifier):
            def _send(self, subject: str, body: str) -> None:
                sent.append((subject, body))

        notifier = Recording(_config(), clock=lambda: clock["now"])
        events = [_event("women"), _event("gender")]
        notifier.notify(events)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], "KEYWORD_DETECTED | women, gender")

        clock["now"] = 50.0
        notifier.notify(events)
        self.assertEqual(len(sent), 1)

        clock["now"] = 200.0
        notifier.notify(events)
        self.assertEqual(len(sent), 2)

    def test_smtp_failure_does_not_arm_cooldown(self) -> None:
        clock = {"now": 10.0}

        class Failing(EmailNotifier):
            def _send(self, subject: str, body: str) -> None:
                raise OSError("smtp down")

        notifier = Failing(_config(), clock=lambda: clock["now"])
        notifier.notify([_event("women")])
        self.assertEqual(notifier._last_sent, {})

    def test_build_notifier_disabled_without_smtp(self) -> None:
        notifier = build_notifier(_config(smtp_host="", smtp_from="", alert_email_to=()))
        self.assertIsInstance(notifier, NullNotifier)
        notifier.notify([_event("women")])


if __name__ == "__main__":
    unittest.main()
