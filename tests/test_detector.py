from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.detector import detect_keywords, mark_keywords_html, mark_keywords_plain
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
            webtv_asset_url="https://webtv.un.org/en/asset/k10/k10h1p03zp",
            speaker="Luiz Inacio Lula da Silva",
        )
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertAlmostEqual(event.video_seconds or 0, 2834.0)
        self.assertEqual(
            event.watch_url,
            "https://www.youtube.com/watch?v=KnIFmbdRCi0",
        )
        self.assertEqual(
            event.webtv_url,
            "https://webtv.un.org/en/asset/k10/k10h1p03zp?kalturaStartTime=2834",
        )
        self.assertEqual(event.speaker, "Luiz Inacio Lula da Silva")
        self.assertIn("women", event.context)

    def test_unreliable_clock_does_not_seek_webtv(self) -> None:
        events = detect_keywords(
            "we must protect women",
            ("women",),
            context_words=4,
            window_start=120.0,
            webtv_asset_url="https://webtv.un.org/en/asset/k1g/k1gb6tjmle",
            timestamp_reliable=False,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0].webtv_url,
            "https://webtv.un.org/en/asset/k1g/k1gb6tjmle",
        )
        self.assertNotIn("kalturaStartTime", events[0].webtv_url or "")

    def test_mark_keywords_wraps_hits(self) -> None:
        text = "Today we want to talk about women and gender."
        self.assertEqual(
            mark_keywords_plain(text, ("women", "gender")),
            "Today we want to talk about **women** and **gender**.",
        )
        html = mark_keywords_html(text, ("women",))
        self.assertIn("<strong>women</strong>", html)
        self.assertNotIn("<script>", html)

    def test_word_boundaries_still_apply(self) -> None:
        self.assertEqual(
            detect_keywords("the superwomen assembled", ("women",), 2),
            [],
        )


if __name__ == "__main__":
    unittest.main()
