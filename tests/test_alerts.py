from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pipeline.alerts import (
    load_alert_config,
    load_speeches,
    simulate_alerts,
    with_excluded_keywords,
)
from pipeline.store import find_existing_speech, speech_to_txt
from pipeline.config import load_session
from pipeline.models import ExtractedSpeech

FIXTURES = Path(__file__).parent / "fixtures" / "alerts"
OUT = FIXTURES / "out"
KEYWORDS = FIXTURES / "keywords.toml"


class AlertKeywordsTest(unittest.TestCase):
    def test_loads_groups_from_toml(self) -> None:
        config = load_alert_config(KEYWORDS)
        self.assertEqual(config.cooldown_seconds, 120)
        self.assertEqual(config.keywords[0], "women")
        self.assertEqual(config.group_for("article 99"), "security_council")

    def test_default_repo_keywords_load(self) -> None:
        config = load_alert_config()
        self.assertIn("women", config.keywords)
        self.assertIn("security council", config.keywords)
        self.assertIn("female candidates", config.keywords)
        self.assertEqual(config.group_for("un80"), "reform")
        self.assertEqual(config.group_for("geopolitics"), "multilateral_order")

    def test_exclude_drops_standalone_reform(self) -> None:
        config = with_excluded_keywords(load_alert_config(KEYWORDS), ("reform",))
        self.assertIn("un reform", config.keywords)
        self.assertNotIn("reform", config.keywords)


class LoadSpeechesTest(unittest.TestCase):
    def test_english_only_skips_spanish(self) -> None:
        speeches, skipped = load_speeches(OUT, 80, english_only=True)
        slugs = [item.slug for item in speeches]
        self.assertEqual(slugs, ["alpha", "beta", "gamma"])
        self.assertEqual(skipped, 1)

    def test_all_languages_keeps_spanish(self) -> None:
        speeches, skipped = load_speeches(OUT, 80, english_only=False)
        self.assertEqual([item.slug for item in speeches], ["alpha", "beta", "delta", "gamma"])
        self.assertEqual(skipped, 0)


class SimulateAlertsTest(unittest.TestCase):
    def test_one_email_per_chunk_and_cooldown(self) -> None:
        config = load_alert_config(KEYWORDS)
        speeches, _ = load_speeches(OUT, 80, english_only=True)
        report = simulate_alerts(
            speeches,
            config,
            expected_by_day={"2025-09-23": 4, "2025-09-24": 10},
            session_id=80,
        )
        by_day = {day.day: day for day in report.days}
        self.assertGreaterEqual(by_day["2025-09-23"].emails, 1)
        self.assertEqual(by_day["2025-09-24"].emails, 1)
        self.assertEqual(by_day["2025-09-24"].speeches, 1)
        self.assertEqual(by_day["2025-09-23"].expected_speeches, 4)
        self.assertTrue(by_day["2025-09-23"].sample_ok)
        self.assertFalse(by_day["2025-09-24"].sample_ok)
        self.assertIsNotNone(by_day["2025-09-23"].projected_emails)
        self.assertGreater(report.emails_per_speech, 0)
        alpha_mail = next(item for item in report.emails if item.slug == "alpha")
        self.assertIn("women", alpha_mail.keywords)
        self.assertLessEqual(len(report.emails), report.hits)
        self.assertGreater(report.emails_by_group["gender"], 0)

    def test_same_keyword_in_one_chunk_is_one_email(self) -> None:
        config = load_alert_config(KEYWORDS)
        from pipeline.alerts import SpeechRecord

        speech = SpeechRecord(
            path=Path("x.txt"),
            session_id=80,
            slug="repeat",
            country="X",
            speaker="X",
            title="President",
            date="2025-09-23",
            source="pdf_en",
            language="en",
            text="women women women women women",
        )
        report = simulate_alerts((speech,), config)
        self.assertEqual(len(report.emails), 1)
        self.assertEqual(report.hits, 5)

    def test_keyword_day_matrix_counts_emails_not_hits(self) -> None:
        config = load_alert_config(KEYWORDS)
        from pipeline.alerts import SpeechRecord, keyword_day_matrix

        speech = SpeechRecord(
            path=Path("x.txt"),
            session_id=80,
            slug="alpha",
            country="X",
            speaker="X",
            title="President",
            date="2025-09-23",
            source="pdf_en",
            language="en",
            text="women women women",
        )
        report = simulate_alerts((speech,), config)
        days, rows = keyword_day_matrix(report)
        self.assertEqual(days, ["2025-09-23"])
        by_keyword = {name: (counts, total) for name, counts, total in rows}
        self.assertEqual(by_keyword["women"], ([1], 1))
        self.assertEqual(report.emails_by_keyword_day["women"]["2025-09-23"], 1)

    def test_cooldown_blocks_second_chunk(self) -> None:
        config = load_alert_config(KEYWORDS)
        from pipeline.alerts import SpeechRecord

        # 120 wpm, 20s chunks, 0 overlap → 40 words/chunk, hop=20s.
        # cooldown 120s → 6 chunks before the same keyword can fire again.
        words = (["hello"] * 39 + ["women"]) * 4
        speech = SpeechRecord(
            path=Path("x.txt"),
            session_id=80,
            slug="paced",
            country="X",
            speaker="X",
            title="President",
            date="2025-09-23",
            source="pdf_en",
            language="en",
            text=" ".join(words),
        )
        report = simulate_alerts((speech,), config)
        self.assertEqual(len(report.emails), 1)

    def test_exclude_reform_reduces_mails(self) -> None:
        config = load_alert_config(KEYWORDS)
        speeches, _ = load_speeches(OUT, 80, english_only=True)
        full = simulate_alerts(speeches, config)
        slim = simulate_alerts(speeches, with_excluded_keywords(config, ("reform",)))
        self.assertLessEqual(len(slim.emails), len(full.emails))
        self.assertNotIn("reform", slim.emails_by_keyword)


class FindExistingSpeechTest(unittest.TestCase):
    def test_finds_txt_in_temp_out(self) -> None:
        config = load_session("80")
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            day = dest / "80" / "2025-09-24"
            day.mkdir(parents=True)
            path = day / "kenya.txt"
            path.write_text(
                speech_to_txt(
                    ExtractedSpeech(
                        session_id=80,
                        slug="kenya",
                        country="Kenya",
                        name="William Ruto",
                        rank="President",
                        speech_date="2025-09-24",
                        source="pdf_en",
                        source_url="https://example/ke_en.pdf",
                        language="en",
                        text="Hello.",
                    )
                ),
                encoding="utf-8",
            )
            found = find_existing_speech(config, "kenya", dest=dest)
            self.assertEqual(found, path)


if __name__ == "__main__":
    unittest.main()
