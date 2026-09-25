from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pipeline.config import load_session
from pipeline.merge import _order_key, merge_speeches, write_merged

OUT = Path(__file__).parent / "fixtures" / "alerts" / "out"


class MergeSpeechesTest(unittest.TestCase):
    def test_keeps_header_of_each_speech(self) -> None:
        speeches = merge_speeches(load_session("80"), dest=OUT)
        self.assertEqual(len(speeches), 4)
        for raw in speeches:
            self.assertTrue(raw.startswith("---\nsession: 80\n"))
            self.assertIn("\ncountry: ", raw)

    def test_english_only_and_day_filter(self) -> None:
        config = load_session("80")
        self.assertEqual(len(merge_speeches(config, dest=OUT, english_only=True)), 3)
        self.assertEqual(len(merge_speeches(config, dest=OUT, speech_date="2025-09-24")), 1)

    def test_orders_by_date_then_numeric_id(self) -> None:
        keys = [
            _order_key("2026-09-22", "M_10", "B"),
            _order_key("", "", "Z"),
            _order_key("2026-09-22", "M_2", "A"),
            _order_key("2026-09-21", "M_99", "C"),
        ]
        self.assertEqual(
            [k[2] for k in sorted(keys)],
            ["C", "A", "B", "Z"],
        )

    def test_write_merged_concatenates_speeches(self) -> None:
        speeches = merge_speeches(load_session("80"), dest=OUT)
        with tempfile.TemporaryDirectory() as tmp:
            text = write_merged(speeches, Path(tmp) / "speeches.txt").read_text(encoding="utf-8")
        self.assertEqual(text.count("\nsession: 80\n"), 4)
        self.assertTrue(text.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
