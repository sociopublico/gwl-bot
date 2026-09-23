from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.espeakers import (
    ListedSpeaker,
    data_url,
    filter_day,
    parse_speakers,
    unique_names,
    write_speakers_txt,
)


FIXTURE = Path(__file__).parent / "fixtures" / "espeakers_data.json"


class EspeakersTest(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_data_url_accepts_hash_page_and_json(self) -> None:
        hash_id = "6aa9a60e2a8f905c7035541515092026"
        want = (
            "https://e-speakers.e-delegate.un.org/"
            f"{hash_id}/data.json"
        )
        self.assertEqual(data_url(hash_id), want)
        self.assertEqual(
            data_url(f"https://e-speakers.e-delegate.un.org/{hash_id}"),
            want,
        )
        self.assertEqual(
            data_url(f"https://e-speakers.e-delegate.un.org/{hash_id}/"),
            want,
        )
        self.assertEqual(data_url(want), want)

    def test_parse_skips_placeholders_and_empty_names(self) -> None:
        speakers = parse_speakers(self.payload)
        names = unique_names(speakers)
        self.assertEqual(
            names,
            [
                "Luiz Inácio Lula da Silva",
                "Donald Trump",
                "Khurelsukh Ukhnaa",
                "Lee Jae Myung",
                "Ahmad Al-Sharaa",
            ],
        )
        korea = [s for s in speakers if "Lee" in s.name][0]
        self.assertEqual(korea.name, "Lee Jae Myung")
        self.assertEqual(korea.day, "2026-09-22")
        self.assertEqual(korea.slot, 2)
        lula = [s for s in speakers if "Lula" in s.name][0]
        self.assertEqual(lula.country, "Brazil")
        self.assertIn("President", lula.title)
        self.assertFalse(any("Tajani" in name for name in names))

    def test_filter_day_and_empty_forthcoming_day(self) -> None:
        speakers = parse_speakers(self.payload)
        day22 = unique_names(filter_day(speakers, "2026-09-22"))
        self.assertEqual(
            day22,
            [
                "Luiz Inácio Lula da Silva",
                "Donald Trump",
                "Khurelsukh Ukhnaa",
                "Lee Jae Myung",
            ],
        )
        self.assertEqual(unique_names(filter_day(speakers, "2026-09-24")), [])
        self.assertEqual(
            unique_names(filter_day(speakers, "2026-09-23")),
            ["Ahmad Al-Sharaa"],
        )

    def test_write_speakers_txt(self) -> None:
        speakers = [
            ListedSpeaker(
                name="Luiz Inácio Lula da Silva",
                country="Brazil",
                title="President of the Federative Republic of Brazil",
                day="2026-09-22",
                meeting="Morning",
                slot=1,
            ),
            ListedSpeaker(
                name="Donald Trump",
                country="United States of America",
                title="President of the United States of America",
                day="2026-09-22",
                meeting="Morning",
                slot=2,
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = write_speakers_txt(
                speakers,
                Path(tmp) / "speakers.txt",
                source="https://e-speakers.e-delegate.un.org/abc",
                day="2026-09-22",
            )
            text = path.read_text(encoding="utf-8")
        self.assertIn("# Auto-exportado desde e-speakers --day 2026-09-22", text)
        self.assertIn(
            "Luiz Inácio Lula da Silva | Brazil | President of the Federative Republic of Brazil",
            text,
        )
        self.assertIn("Donald Trump | United States of America |", text)

    def test_cli_writes_file_and_refuses_empty_day(self) -> None:
        from pipeline.cli import main

        body = FIXTURE.read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "speakers.txt"
            with patch("pipeline.espeakers.fetch", return_value=(200, {}, body)):
                rc = main(
                    [
                        "speakers",
                        "--url",
                        "https://e-speakers.e-delegate.un.org/abc",
                        "--day",
                        "2026-09-22",
                        "--speakers-txt",
                        str(out),
                    ]
                )
            self.assertEqual(rc, 0)
            lines = [
                line
                for line in out.read_text(encoding="utf-8").splitlines()
                if line and not line.startswith("#")
            ]
            self.assertTrue(lines[0].startswith("Luiz Inácio Lula da Silva | Brazil"))
            self.assertTrue(lines[-1].startswith("Lee Jae Myung"))

            with patch("pipeline.espeakers.fetch", return_value=(200, {}, body)):
                rc_empty = main(
                    [
                        "speakers",
                        "--url",
                        "https://e-speakers.e-delegate.un.org/abc",
                        "--day",
                        "2026-09-24",
                        "--speakers-txt",
                        str(out),
                    ]
                )
            self.assertEqual(rc_empty, 2)
            self.assertTrue(lines[0].startswith("Luiz Inácio Lula da Silva | Brazil"))


if __name__ == "__main__":
    unittest.main()
