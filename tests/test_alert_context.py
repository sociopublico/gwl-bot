from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.alert_context import AlertContext, _Line, passage_between
from app.detector import DetectionEvent
from app.transcript import TranscriptSegment


def _event(keyword: str = "women", video_seconds: float | None = 12.0) -> DetectionEvent:
    return DetectionEvent(
        timestamp=datetime(2026, 9, 23, 15, 0, tzinfo=timezone.utc),
        keyword=keyword,
        transcript="talk about women",
        context='"women"',
        video_seconds=video_seconds,
    )


class RecordingNotifier:
    def __init__(self) -> None:
        self.batches: list[list[DetectionEvent]] = []

    @property
    def emails_sent(self) -> int:
        return len(self.batches)

    def notify(self, events) -> None:
        self.batches.append(list(events))

    def notify_status(self, subject: str, body: str) -> bool:
        return False


class PassageBetweenTest(unittest.TestCase):
    def test_drops_segment_already_covered_by_overlap(self) -> None:
        lines = [
            _Line(0, 10, "we talk about peace"),
            _Line(6, 10, "we talk about peace"),
            _Line(10, 18, "and then women"),
        ]
        text = passage_between(lines, 0, 20)
        self.assertEqual(text, "we talk about peace and then women")

    def test_keeps_segment_that_crosses_the_boundary(self) -> None:
        lines = [
            _Line(0, 10, "we talk about peace"),
            _Line(8, 16, "peace and then women"),
        ]
        text = passage_between(lines, 0, 20)
        self.assertIn("we talk about peace", text)
        self.assertIn("peace and then women", text)


class AlertContextTest(unittest.TestCase):
    def test_waits_until_after_window_then_sends_surrounding_text(self) -> None:
        notifier = RecordingNotifier()
        alerts = AlertContext(notifier, before_seconds=10, after_seconds=6)
        alerts.add_window(
            0,
            10,
            "",
            [TranscriptSegment(0, 10, "the chair opened the meeting")],
        )
        alerts.add_window(
            8,
            14,
            "",
            [TranscriptSegment(4, 6, "talk about women")],
        )
        alerts.notify([_event(video_seconds=12)])
        self.assertEqual(notifier.batches, [])

        alerts.add_window(
            12,
            20,
            "",
            [TranscriptSegment(2, 8, "and the speech went on")],
        )
        alerts.flush_ready()
        self.assertEqual(len(notifier.batches), 1)
        passage = notifier.batches[0][0].mail_context or ""
        self.assertIn("the chair opened the meeting", passage)
        self.assertIn("talk about women", passage)
        self.assertIn("and the speech went on", passage)

    def test_same_keyword_while_waiting_does_not_queue_again(self) -> None:
        notifier = RecordingNotifier()
        alerts = AlertContext(notifier, before_seconds=5, after_seconds=30)
        alerts.add_window(0, 10, "talk about women")
        alerts.notify([_event(video_seconds=4)])
        alerts.notify([_event(video_seconds=8)])
        self.assertEqual(notifier.batches, [])
        alerts.flush()
        self.assertEqual(len(notifier.batches), 1)

    def test_flush_sends_partial_context(self) -> None:
        notifier = RecordingNotifier()
        alerts = AlertContext(notifier, before_seconds=5, after_seconds=30)
        alerts.add_window(0, 10, "", [TranscriptSegment(2, 6, "talk about women now")])
        alerts.notify([_event(video_seconds=4)])
        alerts.flush()
        self.assertEqual(len(notifier.batches), 1)
        self.assertIn("women", notifier.batches[0][0].mail_context or "")

    def test_zero_window_forwards_immediately(self) -> None:
        notifier = RecordingNotifier()
        alerts = AlertContext(notifier, before_seconds=0, after_seconds=0)
        alerts.notify([_event()])
        self.assertEqual(len(notifier.batches), 1)
        self.assertIsNone(notifier.batches[0][0].mail_context)


if __name__ == "__main__":
    unittest.main()
