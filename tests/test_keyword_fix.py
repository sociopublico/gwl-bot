from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.keyword_fix import main, parse_fix, run
from pipeline.alerts_site import load_highlights, load_jsonl, merge_records

FIX = "2026-09-25T14:30:00Z..2026-09-25T14:47:20Z=Christophe Mirmand|Monaco"
CONTEXT = '"uphold the law and multilateralism ."'


def _record(stamp: str, speaker: str) -> dict:
    return {
        "timestamp": stamp,
        "day": "2026-09-25",
        "keyword": "multilateralism",
        "speaker": speaker,
        "speaker_title": "Lebanon" if speaker else "",
        "context": CONTEXT,
        "video_seconds": 56.0,
        "timestamp_reliable": False,
        "watch_url": "",
        "webtv_url": "",
    }


class KeywordFixTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        records = [
            _record("2026-09-25T14:09:30Z", "Nawaf Salam"),
            _record("2026-09-25T14:32:21Z", "Nawaf Salam"),
            _record("2026-09-25T14:40:00Z", ""),
        ]
        (self.dir / "keywords.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
            encoding="utf-8",
        )
        (self.dir / "highlights.log").write_text(
            "2026-09-25 14:09:30 | KEYWORD_DETECTED | multilateralism | Nawaf Salam | t=10s? | "
            + CONTEXT
            + "\n2026-09-25 14:30:47 | INFO | SESSION_START | stream monitor\n"
            "2026-09-25 14:32:21 | KEYWORD_DETECTED | multilateralism | Nawaf Salam | t=56s? | "
            + CONTEXT
            + "\n2026-09-25 14:40:00 | KEYWORD_DETECTED | multilateralism | t=56s? | "
            + CONTEXT
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _speakers(self) -> list[str]:
        merged = merge_records(load_jsonl(self.dir / "keywords.jsonl"), load_highlights(self.dir))
        return [record["speaker"] for record in merged]

    def test_parse_fix(self) -> None:
        fix = parse_fix(FIX)
        self.assertEqual((fix.name, fix.title), ("Christophe Mirmand", "Monaco"))
        with self.assertRaises(ValueError):
            parse_fix("2026-09-25T14:47:20Z..2026-09-25T14:30:00Z=X")
        with self.assertRaises(ValueError):
            parse_fix("2026-09-25T14:30:00Z=X")

    def test_dry_run_does_not_write(self) -> None:
        before = (self.dir / "keywords.jsonl").read_text(encoding="utf-8")
        changes = run([parse_fix(FIX)], self.dir, None, apply=False)
        self.assertEqual(len(changes), 4)
        self.assertEqual((self.dir / "keywords.jsonl").read_text(encoding="utf-8"), before)

    def test_apply_keeps_dashboard_deduplicated(self) -> None:
        self.assertEqual(len(self._speakers()), 3)
        self.assertEqual(main(["--fix", FIX, "--log-dir", str(self.dir), "--apply"]), 0)
        self.assertEqual(
            self._speakers(),
            ["Nawaf Salam", "Christophe Mirmand", "Christophe Mirmand"],
        )
        records = load_jsonl(self.dir / "keywords.jsonl")
        self.assertEqual(records[1]["speaker_title"], "Monaco")
        text = (self.dir / "highlights.log").read_text(encoding="utf-8")
        self.assertIn("SESSION_START", text)
        self.assertIn("| multilateralism | Christophe Mirmand | t=56s? |", text)
        self.assertEqual(run([parse_fix(FIX)], self.dir, None, apply=True), [])

    def test_snapshot(self) -> None:
        snaps = self.dir / "alerts"
        snaps.mkdir()
        payload = {"session": 81, "day": "2026-09-25", "hits": [_record("2026-09-25T14:32:21Z", "Nawaf Salam")]}
        (snaps / "2026-09-25.json").write_text(json.dumps(payload), encoding="utf-8")
        run([parse_fix(FIX)], self.dir, snaps, apply=True)
        saved = json.loads((snaps / "2026-09-25.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["hits"][0]["speaker"], "Christophe Mirmand")
        self.assertEqual(saved["session"], 81)


if __name__ == "__main__":
    unittest.main()
