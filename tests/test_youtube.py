from __future__ import annotations

import unittest

from app.youtube import format_timecode, video_id_from_url, watch_url


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


class WatchUrlTest(unittest.TestCase):
    def test_live_page_has_no_timestamp(self) -> None:
        self.assertEqual(
            watch_url("KnIFmbdRCi0"),
            "https://www.youtube.com/watch?v=KnIFmbdRCi0",
        )
        self.assertNotIn("t=", watch_url("KnIFmbdRCi0"))
        self.assertNotIn("embed", watch_url("KnIFmbdRCi0"))


class FormatTimecodeTest(unittest.TestCase):
    def test_minutes_and_hours(self) -> None:
        self.assertEqual(format_timecode(2845), "47:25")
        self.assertEqual(format_timecode(3725), "1:02:05")


if __name__ == "__main__":
    unittest.main()
