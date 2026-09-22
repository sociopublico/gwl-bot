from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from pipeline.alerts_site import (
    build_alerts_payload,
    generate_alerts_site,
)
from pipeline.config import load_session
from pipeline.roster import write_roster


def _roster_entry() -> dict:
    return {
        "slug": "brazil",
        "ficha_url": "https://gadebate.un.org/en/80/brazil",
        "country": "Brazil",
        "name": "Luiz Inacio Lula da Silva",
        "rank": "President",
        "speaker_title": "His Excellency",
        "speech_date": "2026-09-21",
        "chosen": "audio_en",
        "sources": {},
        "error": None,
        "http_status": 200,
    }


class AlertsSiteTest(unittest.TestCase):
    def test_groups_hits_by_day_and_speaker(self) -> None:
        config = load_session("80")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            roster_dir = root / "data" / "roster" / "80"
            write_roster(
                {
                    "session": 80,
                    "day": "2026-09-21",
                    "scraped_at": "now",
                    "speakers": [_roster_entry()],
                },
                roster_dir / "2026-09-21.json",
            )
            cfg = replace(config, root=root)
            records = [
                {
                    "timestamp": "2026-09-21T22:06:53Z",
                    "day": "2026-09-21",
                    "keyword": "world",
                    "speaker": "Luiz Inacio Lula da Silva",
                    "speaker_title": "President",
                    "context": '"the world\'s hope"',
                    "video_seconds": 120,
                    "timestamp_reliable": True,
                    "watch_url": "",
                    "webtv_url": "",
                }
            ]
            payload = build_alerts_payload(cfg, records)
            self.assertEqual(payload["days"][0]["counts"]["hits"], 1)
            speaker = payload["days"][0]["speakers"][0]
            self.assertEqual(speaker["slug"], "brazil")
            self.assertEqual(speaker["hits"][0]["keyword"], "world")
            self.assertIn("world's hope", speaker["hits"][0]["context"])

    def test_writes_html_and_json(self) -> None:
        config = load_session("80")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            logs.mkdir()
            (logs / "keywords.jsonl").write_text(
                json.dumps(
                    {
                        "timestamp": "2026-09-21T22:06:53Z",
                        "day": "2026-09-21",
                        "keyword": "gender",
                        "speaker": "Someone",
                        "context": '"talk about gender"',
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            docs = root / "docs"
            cfg = replace(config, root=root)
            payload = generate_alerts_site(cfg, docs_dir=docs, log_dir=logs)
            self.assertTrue((docs / "alerts.html").is_file())
            data = json.loads((docs / "data" / "alerts.json").read_text(encoding="utf-8"))
            self.assertEqual(data["days"][0]["counts"]["hits"], 1)
            self.assertEqual(payload["days"][0]["speakers"][0]["name"], "Someone")
            html = (docs / "alerts.html").read_text(encoding="utf-8")
            self.assertIn("data/alerts.json", html)
            self.assertNotIn("SPEAKER_CHANGED", html)
            self.assertNotIn("HEALTHCHECK", html)


if __name__ == "__main__":
    unittest.main()
