from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pipeline.config import load_session
from pipeline.merge import merge_speeches, write_merged

OUT = Path(__file__).parent / "fixtures" / "alerts" / "out"


class MergeSpeechesTest(unittest.TestCase):
    def test_merges_all_speeches_with_expected_keys(self) -> None:
        records = merge_speeches(load_session("80"), dest=OUT)
        self.assertEqual(len(records), 4)
        for record in records:
            self.assertEqual(set(record), {"name", "country", "date", "speech"})
            self.assertTrue(record["speech"])
        dates = [r["date"] for r in records]
        self.assertEqual(dates, sorted(dates))

    def test_english_only_and_day_filter(self) -> None:
        config = load_session("80")
        self.assertEqual(len(merge_speeches(config, dest=OUT, english_only=True)), 3)
        self.assertEqual(len(merge_speeches(config, dest=OUT, speech_date="2025-09-24")), 1)

    def test_write_merged_roundtrip(self) -> None:
        records = merge_speeches(load_session("80"), dest=OUT)
        with tempfile.TemporaryDirectory() as tmp:
            path = write_merged(records, Path(tmp) / "speeches.json")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), records)


if __name__ == "__main__":
    unittest.main()
