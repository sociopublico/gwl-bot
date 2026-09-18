from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.detector import DetectionEvent
from app.events import DetectionDeduper, emit_detections
from app.notifier import NullNotifier


def _event(keyword: str, context: str) -> DetectionEvent:
    return DetectionEvent(
        timestamp=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
        keyword=keyword,
        transcript="talk about women",
        context=context,
    )


class DetectionDeduperTest(unittest.TestCase):
    def test_drops_same_keyword_and_context_within_ttl(self) -> None:
        deduper = DetectionDeduper(ttl_seconds=30)
        first = deduper.filter([_event("women", '"...women..."')], now=10.0)
        second = deduper.filter([_event("women", '"...women..."')], now=15.0)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0)

    def test_allows_after_ttl(self) -> None:
        deduper = DetectionDeduper(ttl_seconds=10)
        deduper.filter([_event("women", '"...women..."')], now=10.0)
        again = deduper.filter([_event("women", '"...women..."')], now=25.0)
        self.assertEqual(len(again), 1)

    def test_emit_detections_uses_deduper(self) -> None:
        deduper = DetectionDeduper(ttl_seconds=30)
        notifier = NullNotifier()
        events = [_event("women", '"...women..."')]
        first = emit_detections(events, notifier, deduper=deduper)
        second = emit_detections(events, notifier, deduper=deduper)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0)


if __name__ == "__main__":
    unittest.main()
