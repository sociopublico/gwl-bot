from __future__ import annotations

import unittest

from app.clock import StreamClock, pcm_seconds


class PcmSecondsTest(unittest.TestCase):
    def test_s16le_mono(self) -> None:
        self.assertEqual(pcm_seconds(320_000, 16_000), 10.0)


class StreamClockTest(unittest.TestCase):
    def test_first_window_starts_at_origin(self) -> None:
        clock = StreamClock(origin_seconds=0, sample_rate=16_000)
        window = 320_000
        clock.add_fresh(window)
        self.assertEqual(clock.window_start(window), 0.0)

    def test_overlap_does_not_advance_twice(self) -> None:
        clock = StreamClock(origin_seconds=0, sample_rate=16_000)
        window = 320_000  # 10s
        hop = 288_000  # 9s
        clock.add_fresh(window)
        clock.add_fresh(hop)
        self.assertAlmostEqual(clock.fresh_seconds, 19.0)
        self.assertAlmostEqual(clock.window_start(window), 9.0)

    def test_vod_origin_and_segment_offset(self) -> None:
        clock = StreamClock(origin_seconds=2830, sample_rate=16_000)
        window = 320_000
        clock.add_fresh(window)
        self.assertAlmostEqual(clock.window_start(window), 2830.0)
        self.assertAlmostEqual(clock.video_seconds(window, 4.0), 2834.0)


if __name__ == "__main__":
    unittest.main()
