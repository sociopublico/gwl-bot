from __future__ import annotations

from pathlib import Path
from unittest import TestCase

from pipeline.metadata import apply_protocol, clear_protocol_index
from pipeline.models import ExtractedSpeech
from pipeline.protocol import (
    index_from_records,
    parse_protocol_layout,
    rank_to_level,
)

FIXTURE = Path(__file__).parent / "fixtures" / "protocol_layout.txt"


def _speech(**overrides) -> ExtractedSpeech:
    data = dict(
        session_id=81,
        slug="kenya",
        country="Kenya",
        name="William Ruto",
        rank="President",
        speech_date="2026-09-22",
        source="pdf_en",
        source_url="https://example/ke.pdf",
        language="en",
        text="hello",
    )
    data.update(overrides)
    return ExtractedSpeech(**data)


class ProtocolParserTest(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.countries = parse_protocol_layout(FIXTURE.read_text(encoding="utf-8"))
        cls.by_name = {item.country.upper(): item for item in cls.countries}

    def test_parses_core_ung81_examples(self) -> None:
        albania = self.by_name["ALBANIA"]
        self.assertEqual(
            [(p.level, p.name, p.gender) for p in albania.people],
            [
                ("HS", "Bajram Begaj", "male"),
                ("HG", "Edi Rama", "male"),
                ("CD", "Ferit Hoxha", "male"),
            ],
        )
        brazil = self.by_name["BRAZIL"]
        self.assertEqual(brazil.by_level("HS").name, "Luiz Inácio Lula da Silva")
        self.assertIsNone(brazil.by_level("HG"))
        self.assertEqual(brazil.by_level("CD").name, "Mauro Luiz Iecker Vieira")

        angola = self.by_name["ANGOLA"]
        self.assertEqual(angola.by_level("HS").name, "João Manuel Gonçalves Lourenço")
        self.assertEqual(angola.by_level("HG").name, angola.by_level("HS").name)

        argentina = self.by_name["ARGENTINA"]
        self.assertEqual(argentina.by_level("HS").name, "Javier Gerardo Milei")
        self.assertEqual(argentina.by_level("HS").gender, "male")
        self.assertIsNone(argentina.by_level("HG"))

        austria = self.by_name["AUSTRIA"]
        self.assertEqual(austria.by_level("CD").name, "Beate Meinl-Reisinger")
        self.assertEqual(austria.by_level("CD").gender, "female")
        self.assertEqual(austria.by_level("CD").pronouns, "she/her")

        kenya = self.by_name["KENYA"]
        self.assertEqual(kenya.by_level("HS").name, "William Samoei Ruto")
        self.assertEqual(kenya.by_level("HG").name, "William Samoei Ruto")

        civ = self.by_name["CÔTE D'IVOIRE"]
        self.assertEqual(civ.by_level("HS").name, "Alassane Ouattara")
        self.assertEqual(civ.by_level("CD").name, "Nialé Kaba")
        self.assertEqual(civ.by_level("CD").gender, "female")

    def test_commonwealth_keeps_gg_and_monarch(self) -> None:
        australia = self.by_name["AUSTRALIA"]
        names = [(p.level, p.name, p.gender) for p in australia.people]
        self.assertIn(("HS", "King Charles III", "male"), names)
        self.assertIn(("HS", "Sam Mostyn", "female"), names)
        self.assertEqual(australia.by_level("HS").name, "Sam Mostyn")
        self.assertEqual(australia.by_level("HG").name, "Anthony Albanese")
        self.assertEqual(australia.by_level("CD").name, "Penny Wong")
        self.assertEqual(australia.by_level("CD").gender, "female")

        barbados = self.by_name["BARBADOS"]
        self.assertEqual(barbados.by_level("HG").name, "Mia Amor Mottley")
        self.assertEqual(barbados.by_level("HG").gender, "female")


class ProtocolMatchTest(TestCase):
    def test_matches_by_name_or_rank(self) -> None:
        idx = index_from_records(
            [
                {
                    "country": "Australia",
                    "people": [
                        {
                            "level": "HS",
                            "name": "King Charles III",
                            "gender": "male",
                            "pronouns": "he/him",
                        },
                        {
                            "level": "HS",
                            "name": "Sam Mostyn",
                            "gender": "female",
                            "pronouns": "she/her",
                        },
                        {
                            "level": "HG",
                            "name": "Anthony Albanese",
                            "gender": "male",
                            "pronouns": "he/him",
                        },
                    ],
                }
            ]
        )
        pm = idx.match_speaker(
            slug="australia",
            country="Australia",
            name="Anthony Albanese",
            rank="Prime Minister",
        )
        self.assertEqual(pm.level, "HG")
        gg = idx.match_speaker(
            slug="australia",
            country="Australia",
            name="Sam Mostyn",
            rank="Governor-General",
        )
        self.assertEqual(gg.gender, "female")
        self.assertEqual(rank_to_level("Governor-General"), "HS")

    def test_apply_protocol_overrides_heuristics(self) -> None:
        records = [
            {
                "country": "Côte d'Ivoire",
                "people": [
                    {
                        "level": "CD",
                        "name": "Nialé Kaba",
                        "gender": "female",
                        "pronouns": "she/her",
                    }
                ],
            }
        ]
        idx = index_from_records(records)
        clear_protocol_index()
        import pipeline.metadata as metadata

        metadata._PROTOCOL_INDEX = idx
        try:
            level, pronouns, gender = apply_protocol(
                _speech(
                    slug="cote-divoire",
                    country="Côte d'Ivoire",
                    name="Nialé Kaba",
                    rank="Minister for Foreign Affairs",
                    speaker_title="",
                ),
                level="CD",
                pronouns="",
                gender="",
            )
        finally:
            clear_protocol_index()
        self.assertEqual((level, pronouns, gender), ("CD", "she/her", "female"))
