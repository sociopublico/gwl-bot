from __future__ import annotations

import unittest

from app.youtube import format_timecode, video_id_from_url, watch_page_url_at, watch_url_at


class VideoIdFromUrlTest(unittest.TestCase):
    def test_watch_url(self) -> None:
        self.assertEqual(
            video_id_from_url("https://www.youtube.com/watch?v=KnIFmbdRCi0"),
            "KnIFmbdRCi0",
        )

    def test_short_url(self) -> None:
        self.assertEqual(video_id_from_url("https://youtu.be/KnIFmbdRCi0"), "KnIFmbdRCi0")

    def test_live_path(self) -> None:
        self.assertEqual(
            video_id_from_url("https://www.youtube.com/live/KnIFmbdRCi0"),
            "KnIFmbdRCi0",
        )

    def test_rejects_non_youtube(self) -> None:
        self.assertIsNone(video_id_from_url("https://example.com/watch?v=KnIFmbdRCi0"))


class WatchUrlAtTest(unittest.TestCase):
    def test_rounds_to_int_seconds(self) -> None:
        self.assertEqual(
            watch_url_at("KnIFmbdRCi0", 2830.4),
            "https://www.youtube.com/embed/KnIFmbdRCi0?start=2830",
        )

    def test_clamps_negative(self) -> None:
        self.assertEqual(
            watch_url_at("KnIFmbdRCi0", -3),
            "https://www.youtube.com/embed/KnIFmbdRCi0?start=0",
        )

    def test_watch_page_keeps_t_seconds(self) -> None:
        self.assertEqual(
            watch_page_url_at("KnIFmbdRCi0", 2830.4),
            "https://www.youtube.com/watch?v=KnIFmbdRCi0&t=2830",
        )


class FormatTimecodeTest(unittest.TestCase):
    def test_minutes_and_hours(self) -> None:
        self.assertEqual(format_timecode(2845), "47:25")
        self.assertEqual(format_timecode(3725), "1:02:05")


if __name__ == "__main__":
    unittest.main()
