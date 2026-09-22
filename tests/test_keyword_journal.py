from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.detector import DetectionEvent
from app.keyword_journal import (
    KeywordJournal,
    event_to_record,
    parse_highlights_line,
)


def _event(**overrides) -> DetectionEvent:
    values = dict(
        timestamp=datetime(2026, 9, 21, 22, 6, 53, tzinfo=timezone.utc),
        keyword="world",
        transcript="the world's hope for peace",
        context='"the world\'s hope"',
        speaker="Luiz Inacio Lula da Silva",
        speaker_title="President of Brazil",
        video_seconds=120.0,
        timestamp_reliable=True,
    )
    values.update(overrides)
    return DetectionEvent(**values)


class ParseHighlightsTest(unittest.TestCase):
    def test_parses_keyword_speaker_time_context(self) -> None:
        line = (
            '2026-09-21 22:06:53 | KEYWORD_DETECTED | world | '
            'Luiz Inacio Lula da Silva | t=120s | "...the world\'s hope..."'
        )
        record = parse_highlights_line(line)
        assert record is not None
        self.assertEqual(record["keyword"], "world")
        self.assertEqual(record["speaker"], "Luiz Inacio Lula da Silva")
        self.assertEqual(record["video_seconds"], 120.0)
        self.assertTrue(record["timestamp_reliable"])
        self.assertEqual(record["day"], "2026-09-21")

    def test_ignores_speaker_changed(self) -> None:
        line = (
            "2026-09-21 22:06:53 | SPEAKER_CHANGED | Luiz Inacio Lula da Silva"
        )
        self.assertIsNone(parse_highlights_line(line))

    def test_marks_unreliable_time(self) -> None:
        line = '2026-09-21 22:06:53 | KEYWORD_DETECTED | world | t=12s? | "...world..."'
        record = parse_highlights_line(line)
        assert record is not None
        self.assertFalse(record["timestamp_reliable"])
        self.assertEqual(record["speaker"], "")


class KeywordJournalTest(unittest.TestCase):
    def test_appends_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "keywords.jsonl"
            KeywordJournal(path).append(_event())
            rows = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(rows), 1)
            payload = json.loads(rows[0])
            self.assertEqual(payload["keyword"], "world")
            self.assertEqual(payload["day"], "2026-09-21")
            self.assertEqual(event_to_record(_event())["speaker"], payload["speaker"])


if __name__ == "__main__":
    unittest.main()
