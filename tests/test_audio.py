from __future__ import annotations

import unittest

from app.audio import _ffmpeg_command, _live_origin_seconds, apply_overlap


class ApplyOverlapTest(unittest.TestCase):
    def test_first_chunk_keeps_tail(self) -> None:
        fresh = b"abcdefghij"
        window, tail = apply_overlap(b"", fresh, overlap_bytes=4)
        self.assertEqual(window, fresh)
        self.assertEqual(tail, b"ghij")

    def test_second_chunk_prepends_tail(self) -> None:
        window, tail = apply_overlap(b"ghij", b"klmnopqrst", overlap_bytes=4)
        self.assertEqual(window, b"ghijklmnopqrst")
        self.assertEqual(tail, b"qrst")

    def test_zero_overlap_does_not_carry(self) -> None:
        window, tail = apply_overlap(b"", b"abc", overlap_bytes=0)
        self.assertEqual(window, b"abc")
        self.assertEqual(tail, b"")

    def test_short_window_carries_everything(self) -> None:
        window, tail = apply_overlap(b"", b"ab", overlap_bytes=8)
        self.assertEqual(window, b"ab")
        self.assertEqual(tail, b"ab")


class FfmpegCommandTest(unittest.TestCase):
    def test_seek_is_inserted_before_input(self) -> None:
        cmd = _ffmpeg_command("https://example.com/audio.m3u8", {}, start_seconds=2830)
        ss_at = cmd.index("-ss")
        input_at = cmd.index("-i")
        self.assertLess(ss_at, input_at)
        self.assertEqual(cmd[ss_at + 1], "2830.000")

    def test_no_seek_when_start_is_zero(self) -> None:
        cmd = _ffmpeg_command("https://example.com/audio.m3u8", {}, start_seconds=0)
        self.assertNotIn("-ss", cmd)


class LiveOriginTest(unittest.TestCase):
    def test_uses_release_timestamp(self) -> None:
        origin, reliable = _live_origin_seconds({"release_timestamp": 1000}, now=1300)
        self.assertEqual(origin, 300)
        self.assertTrue(reliable)

    def test_unknown_start_is_unreliable(self) -> None:
        origin, reliable = _live_origin_seconds({}, now=1300)
        self.assertEqual(origin, 0.0)
        self.assertFalse(reliable)

    def test_ignores_stale_24_7_listing_date(self) -> None:
        # 2025-08-26 listing vs 2026-09-21 now, like UNTV 24/7.
        origin, reliable = _live_origin_seconds(
            {"release_timestamp": 1756226452},
            now=1790028413,
        )
        self.assertEqual(origin, 0.0)
        self.assertFalse(reliable)


if __name__ == "__main__":
    unittest.main()
