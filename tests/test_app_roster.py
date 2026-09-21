from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.roster import (
    country_from_title,
    country_score,
    load_roster_file,
    match_roster,
    parse_roster,
)


ROSTER = (
    "Luiz Inácio Lula da Silva | Brazil | President of the Federative Republic of Brazil\n"
    "Donald Trump | United States of America | President of the United States of America\n"
    "Anita Anand | Canada | Minister of Foreign Affairs of Canada\n"
    "Emmanuel Macron | France | President of the French Republic\n"
)


class ParseRosterTest(unittest.TestCase):
    def test_parses_name_only_and_pipe_rows(self) -> None:
        entries = parse_roster("Luiz Inacio Lula da Silva;Emmanuel Macron | France | President")
        self.assertEqual(entries[0].name, "Luiz Inacio Lula da Silva")
        self.assertEqual(entries[0].country, "")
        self.assertEqual(entries[1].name, "Emmanuel Macron")
        self.assertEqual(entries[1].country, "France")
        self.assertEqual(entries[1].title, "President")

    def test_load_file_skips_comments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "speakers.txt"
            path.write_text("# comment\n" + ROSTER, encoding="utf-8")
            entries = load_roster_file(str(path))
        self.assertEqual(entries[0].country, "Brazil")
        self.assertEqual(len(entries), 4)


class CountryMatchTest(unittest.TestCase):
    def test_country_from_title(self) -> None:
        self.assertEqual(
            country_from_title("President of the Federative Republic of Brazil"),
            "Federative Republic of Brazil",
        )
        self.assertEqual(country_from_title("Prime Minister of Canada"), "Canada")

    def test_brasil_alias_matches_brazil(self) -> None:
        self.assertGreaterEqual(country_score("brasil", "Brazil"), 0.9)
        self.assertGreaterEqual(
            country_score("Federative Republic of Brazil", "Brazil"),
            0.9,
        )


class MatchRosterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.roster = parse_roster(ROSTER)

    def test_name_still_wins(self) -> None:
        hit = match_roster("Lewis Nasier Lula da Silva", self.roster, 0.62)
        assert hit is not None
        self.assertEqual(hit[0].name, "Luiz Inácio Lula da Silva")

    def test_president_of_brasil_maps_to_lula(self) -> None:
        hit = match_roster(
            "",
            self.roster,
            0.62,
            title="President of Brasil",
            country="Brasil",
        )
        assert hit is not None
        self.assertEqual(hit[0].name, "Luiz Inácio Lula da Silva")

    def test_prime_minister_of_canada(self) -> None:
        hit = match_roster(
            "unknown",
            self.roster,
            0.62,
            title="Prime Minister of Canada",
        )
        assert hit is not None
        self.assertEqual(hit[0].name, "Anita Anand")

    def test_does_not_guess_when_country_missing(self) -> None:
        self.assertIsNone(match_roster("", self.roster, 0.62, title="President"))


if __name__ == "__main__":
    unittest.main()
