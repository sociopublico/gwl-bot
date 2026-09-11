from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.roster import load_roster_file, match_roster, parse_roster, roster_score


ROSTER = (
    "Luiz Inacio Lula da Silva",
    "Emmanuel Macron",
    "Donald Trump",
    "Yoon Suk Yeol",
)


class RosterMatchTest(unittest.TestCase):
    def test_maps_whisper_lula_variants(self) -> None:
        for asr in (
            "Louis Narsier-Loula Dar Silva",
            "Lewis Nasier-Loula Dare Silva",
            "Luis Narsio Lula da Silva",
        ):
            matched = match_roster(asr, ROSTER, threshold=0.62)
            assert matched is not None
            self.assertEqual(matched[0], "Luiz Inacio Lula da Silva")

    def test_does_not_map_macron_to_lula(self) -> None:
        matched = match_roster("Emmanuel Macron", ROSTER, threshold=0.62)
        assert matched is not None
        self.assertEqual(matched[0], "Emmanuel Macron")

    def test_unknown_name_below_threshold(self) -> None:
        self.assertIsNone(match_roster("Angela Merkel", ROSTER, threshold=0.62))

    def test_lula_beats_unrelated_silva(self) -> None:
        lula = roster_score("Louis Narsier-Loula Dar Silva", "Luiz Inacio Lula da Silva")
        other = roster_score("Louis Narsier-Loula Dar Silva", "Emmanuel Macron")
        self.assertGreater(lula, other)
        self.assertGreater(lula, 0.62)

    def test_erdogan_whisper_commas_against_full_roster(self) -> None:
        asr = "red-chap tie-jip Erdogan"
        roster = parse_roster(
            (Path(__file__).resolve().parents[1] / "speakers.txt").read_text(encoding="utf-8")
        )
        matched = match_roster(asr, roster, threshold=0.62)
        assert matched is not None
        self.assertEqual(matched[0], "Recep Tayyip Erdoğan")

    def test_parse_and_file(self) -> None:
        parsed = parse_roster("Luiz Inacio Lula da Silva;Emmanuel Macron\n# comment\nDonald Trump")
        self.assertEqual(parsed, ROSTER[:3])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "speakers.txt"
            path.write_text("Yoon Suk Yeol\n", encoding="utf-8")
            self.assertEqual(load_roster_file(str(path)), ("Yoon Suk Yeol",))
            self.assertEqual(load_roster_file(str(Path(tmp) / "missing.txt")), ())


if __name__ == "__main__":
    unittest.main()
