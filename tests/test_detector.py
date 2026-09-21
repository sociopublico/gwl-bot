from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.detector import detect_keywords
from app.transcript import TranscriptSegment


class DetectKeywordsTest(unittest.TestCase):
    def test_maps_keyword_to_segment_timestamp(self) -> None:
        segments = [
            TranscriptSegment(0.0, 4.0, "Hello everyone"),
            TranscriptSegment(4.0, 8.0, "we must protect women and children"),
        ]
        events = detect_keywords(
            "Hello everyone we must protect women and children",
            ("women",),
            context_words=20,
            timestamp=datetime(2026, 9, 1, 15, 32, 17, tzinfo=timezone.utc),
            segments=segments,
            window_start=2830.0,
            video_id="KnIFmbdRCi0",
            speaker="Luiz Inacio Lula da Silva",
        )
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertAlmostEqual(event.video_seconds or 0, 2834.0)
        self.assertEqual(
            event.watch_url,
            "https://www.youtube.com/embed/KnIFmbdRCi0?start=2834",
        )
        self.assertEqual(event.speaker, "Luiz Inacio Lula da Silva")
        self.assertIn("women", event.context)

    def test_word_boundaries_still_apply(self) -> None:
        self.assertEqual(
            detect_keywords("the superwomen assembled", ("women",), 2),
            [],
        )


if __name__ == "__main__":
    unittest.main()
