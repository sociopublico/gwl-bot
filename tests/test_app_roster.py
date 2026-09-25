from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.roster import (
    RosterEntry,
    country_from_title,
    country_score,
    has_country_words,
    load_roster_file,
    load_roster_json,
    match_roster,
    merge_rosters,
    parse_roster,
    parse_roster_json,
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

    def test_united_nations_does_not_match_united_states(self) -> None:
        self.assertLess(
            country_score(
                "Erie First Session of the United Nations General Assembly",
                "United States of America",
            ),
            0.86,
        )
        self.assertGreaterEqual(
            country_score("United States", "United States of America"),
            0.9,
        )
        hit = match_roster(
            "Khalilur Rahman on his election as",
            self.roster,
            0.62,
            title="president of the Erie First Session of the United Nations General Assembly",
            country="Erie First Session of the United Nations General Assembly",
        )
        self.assertIsNone(hit)


class CountryAliasTest(unittest.TestCase):
    def test_demonyms_and_official_forms(self) -> None:
        pairs = [
            ("Council of Ministers of the Lebanese Republic", "Lebanon"),
            ("Hellenic Republic", "Greece"),
            ("Swiss Confederation", "Switzerland"),
            ("French Republic", "France"),
            ("Argentine Republic", "Argentina"),
            ("Kirgis Republic", "Kyrgyzstan"),
            ("Republic of Maltives", "Maldives"),
            ("Principality of Endora", "Andorra"),
        ]
        for extracted, canonical in pairs:
            self.assertGreaterEqual(country_score(extracted, canonical), 0.86, extracted)

    def test_demonym_does_not_match_other_country(self) -> None:
        self.assertLess(country_score("Lebanese Republic", "Libya"), 0.86)

    def test_lebanon_country_only_match(self) -> None:
        roster = parse_roster("Nawaf Salam | Lebanon;Ali Falih Al-Zaidi | Iraq")
        hit = match_roster(
            "Nawab Salaam",
            roster,
            0.62,
            title="President of the Council of Ministers of the Lebanese Republic",
            country="Council of Ministers of the Lebanese Republic",
        )
        assert hit is not None
        self.assertEqual(hit[0].name, "Nawaf Salam")

    def test_shared_words_do_not_make_countries_ambiguous(self) -> None:
        roster = parse_roster(
            "Hilda Heine | Marshall Islands;"
            "Matthew Cooper Wale | Solomon Islands;"
            "James Marape | Papua New Guinea;"
            "Ilídio Vieira Té | Guinea-Bissau;"
            "Philip Pierre | Saint Lucia;"
            "Geoffrey Hanley | Saint Kitts and Nevis;"
            "Godwin Friday | Saint Vincent and the Grenadines"
        )
        cases = {
            "Republic of the Marsh Islands": "Hilda Heine",
            "Solomon Islands": "Matthew Cooper Wale",
            "Papua New Guinea": "James Marape",
            "Republic of Guinea-Bissau": "Ilídio Vieira Té",
            "St Lucia": "Philip Pierre",
            "Federation of Saint Kitts and Nevis": "Geoffrey Hanley",
            "Saint Vincent and the Grenadines": "Godwin Friday",
        }
        for country, expected in cases.items():
            hit = match_roster("", roster, 0.62, title=f"President of {country}", country=country)
            assert hit is not None, country
            self.assertEqual(hit[0].name, expected, country)
        self.assertLess(country_score("Marsh Islands", "Solomon Islands"), 0.86)

    def test_has_country_words(self) -> None:
        self.assertTrue(has_country_words("Council of Ministers of the Lebanese Republic"))
        self.assertTrue(has_country_words("South Africa"))
        self.assertFalse(has_country_words("General Assembly"))
        self.assertFalse(has_country_words("81st"))
        self.assertFalse(has_country_words("the"))


class RosterJsonTest(unittest.TestCase):
    def test_parses_pipeline_payload_and_drops_leaked_names(self) -> None:
        payload = {
            "speakers": [
                {"country": "Kiribati", "name": "Taneti Maamau", "rank": "President"},
                {"country": "Iraq", "name": "Ali Falih Al-Zaidi", "speaker_title": "His Excellency"},
                {"country": "Chad", "name": "Widget Name"},
                {"country": "Togo", "name": "Widget Name"},
                {"country": "Norway", "name": "Widget Name"},
                {"country": "Monaco", "name": ""},
            ]
        }
        entries = parse_roster_json(payload)
        self.assertEqual(entries[0], RosterEntry("Taneti Maamau", "Kiribati", "President"))
        self.assertEqual(entries[1].title, "")
        names = [entry.name for entry in entries]
        self.assertNotIn("Widget Name", names)
        self.assertIn("Chad", names)
        self.assertIn("Monaco", names)

    def test_load_missing_or_broken_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_roster_json(Path(tmp) / "nope.json"), ())
            broken = Path(tmp) / "broken.json"
            broken.write_text("{", encoding="utf-8")
            self.assertEqual(load_roster_json(broken), ())

    def test_merge_dedupes_by_folded_name(self) -> None:
        merged = merge_rosters(
            parse_roster("Micheál Martin | Ireland"),
            parse_roster("Micheal Martin | Ireland;Robert Abela | Malta"),
        )
        self.assertEqual([entry.name for entry in merged], ["Micheál Martin", "Robert Abela"])


if __name__ == "__main__":
    unittest.main()
