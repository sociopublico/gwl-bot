from __future__ import annotations

from unittest import TestCase

from pipeline.translate import _chunks


class TranslateChunksTest(TestCase):
    def test_keeps_short_text_together(self) -> None:
        text = "Hello world.\n\nSecond paragraph."
        self.assertEqual(_chunks(text), ["Hello world.\n\nSecond paragraph."])

    def test_splits_long_paragraphs(self) -> None:
        para = "x" * 5000
        parts = _chunks(para)
        self.assertGreaterEqual(len(parts), 2)
        self.assertEqual("".join(parts), para)
        self.assertTrue(all(len(p) <= 2500 for p in parts))
