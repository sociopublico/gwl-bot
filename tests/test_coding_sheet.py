from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from pipeline.cli import main
from pipeline.coding import EMERGING_COLUMNS, INDICATOR_COLUMNS
from pipeline.coding_sheet import publish_coding_day
from pipeline.config import load_session
from pipeline.models import ExtractedSpeech
from pipeline.sheets import (
    EMERGING_SHEET_COLUMNS,
    INDICATORS_SHEET_COLUMNS,
    SheetsSettings,
    append_coding_rows,
    ensure_header_columns,
)
from pipeline.store import out_dir, write_speech


def _speech(**kwargs) -> ExtractedSpeech:
    data = dict(
        session_id=80,
        slug="angola",
        country="Angola",
        name="João Lourenço",
        rank="President",
        speech_date="2025-09-23",
        source="pdf_en",
        source_url="https://example/80_AO_EN.pdf",
        language="en",
        text="Madam President, Angola speaks today.",
        speaker_title="His Excellency",
        original_language="en",
        transformation="none",
        id_speech="M_1",
    )
    data.update(kwargs)
    return ExtractedSpeech(**data)


def _settings() -> SheetsSettings:
    return SheetsSettings(
        spreadsheet_id="test-sheet",
        metadata_tab="Metadata",
        analysis_tab="Analysis",
        country_tab="country_list",
        indicators_tab="Indicators",
        emerging_tab="Emerging_Priorities",
        credentials_path="",
        country_csv="",
    )


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in columns})


class FakeWorksheet:
    def __init__(self, values: list[list[str]]):
        self._values = [list(row) for row in values]
        self.updates: list[tuple] = []
        self.appends: list[list[list[str]]] = []

    def get_all_values(self) -> list[list[str]]:
        return [list(row) for row in self._values]

    def update(self, rng, data, **kwargs):
        self.updates.append((rng, data, kwargs))
        if rng == "A1":
            headers = list(data[0])
            if self._values:
                self._values[0] = headers
            else:
                self._values = [headers]

    def append_rows(self, payload, **kwargs):
        self.appends.append([list(row) for row in payload])
        self._values.extend(list(row) for row in payload)


class HeaderColumnsTest(unittest.TestCase):
    def test_appends_date_and_country_at_end(self) -> None:
        headers = list(INDICATORS_SHEET_COLUMNS)
        out = ensure_header_columns(headers, ("date", "country"))
        self.assertEqual(out[-2:], ["date", "country"])
        self.assertEqual(out[:-2], headers)
        self.assertEqual(ensure_header_columns(out, ("date", "country")), out)


class AppendCodingRowsTest(unittest.TestCase):
    def test_skips_existing_key_and_extends_header(self) -> None:
        ws = FakeWorksheet(
            [
                list(INDICATORS_SHEET_COLUMNS),
                [
                    "M_5",
                    "M_5_gender_equality_position",
                    "Gender and women's leadership",
                    "gender_equality_position",
                    "1",
                    "Positive / Supportive",
                    "Old extract",
                    "",
                    "2025-09-24",
                ],
            ]
        )
        written, skipped = append_coding_rows(
            [
                {
                    "id_speech": "M_5",
                    "extract_id": "M_5_gender_equality_position",
                    "cluster": "Gender and women's leadership",
                    "indicator_name": "gender_equality_position",
                    "code": "3",
                    "option": "Negative / Pushback",
                    "textual_extract": "should not overwrite",
                    "coder_notes": "",
                    "date_coded": "2026-09-15",
                    "date": "2025-09-23",
                    "country": "Angola",
                    "coding_source": "claude",
                    "model": "claude-haiku-4-5",
                },
                {
                    "id_speech": "M_1",
                    "extract_id": "M_1_gender_equality_position",
                    "cluster": "Gender and women's leadership",
                    "indicator_name": "gender_equality_position",
                    "code": "0",
                    "option": "No Mention",
                    "textual_extract": "",
                    "coder_notes": "",
                    "date_coded": "2026-09-15",
                    "date": "2025-09-23",
                    "country": "Angola",
                    "coding_source": "claude",
                    "model": "claude-haiku-4-5",
                },
            ],
            tab="Indicators",
            key_column="extract_id",
            default_headers=list(INDICATORS_SHEET_COLUMNS),
            settings=_settings(),
            ws=ws,
        )
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["extract_id"], "M_5_gender_equality_position")
        self.assertEqual(len(written), 1)
        self.assertEqual(ws.updates[0][1][0][-2:], ["date", "country"])
        payload = ws.appends[0]
        headers = ws.updates[0][1][0]
        self.assertNotIn("coding_source", headers)
        self.assertNotIn("model", headers)
        self.assertEqual(payload[0][headers.index("extract_id")], "M_1_gender_equality_position")
        self.assertEqual(payload[0][headers.index("date")], "2025-09-23")
        self.assertEqual(payload[0][headers.index("country")], "Angola")
        self.assertEqual(ws._values[1][1], "M_5_gender_equality_position")
        self.assertEqual(ws._values[1][4], "1")

    def test_dry_run_does_not_write(self) -> None:
        ws = FakeWorksheet([list(EMERGING_SHEET_COLUMNS)])
        written, skipped = append_coding_rows(
            [
                {
                    "id_speech": "M_1",
                    "id_extract": "M_1_climate_change",
                    "emerging_topic": "Climate change",
                    "textual_extract": "Climate crisis.",
                    "date": "2025-09-23",
                    "country": "Angola",
                }
            ],
            tab="Emerging_Priorities",
            key_column="id_extract",
            default_headers=list(EMERGING_SHEET_COLUMNS),
            settings=_settings(),
            ws=ws,
            dry_run=True,
        )
        self.assertEqual(len(written), 1)
        self.assertEqual(skipped, [])
        self.assertEqual(ws.updates, [])
        self.assertEqual(ws.appends, [])


class PublishCodingDayTest(unittest.TestCase):
    def test_missing_csv_asks_to_run_coding(self) -> None:
        config = load_session("80")
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                rc = publish_coding_day(
                    config,
                    day="2025-09-23",
                    dest=dest,
                    settings=_settings(),
                    worksheets={"Indicators": FakeWorksheet([]), "Emerging_Priorities": FakeWorksheet([])},
                )
            self.assertEqual(rc, 1)
            self.assertIn("corré coding primero", stderr.getvalue())

    def test_skips_existing_extract_and_appends_date_country(self) -> None:
        config = load_session("80")
        settings = _settings()
        indicators_ws = FakeWorksheet(
            [
                list(INDICATORS_SHEET_COLUMNS),
                [
                    "M_5",
                    "M_5_gender_equality_position",
                    "Gender and women's leadership",
                    "gender_equality_position",
                    "1",
                    "Positive / Supportive",
                    "Already on sheet",
                    "",
                    "2025-09-24",
                ],
            ]
        )
        emerging_ws = FakeWorksheet([list(EMERGING_SHEET_COLUMNS)])
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            directory = out_dir(config, "2025-09-23", dest=dest)
            write_speech(_speech(), directory)
            write_speech(
                _speech(slug="brazil", country="Brazil", name="Lula", id_speech="M_2"),
                directory,
            )
            _write_csv(
                directory / "Indicators.csv",
                INDICATOR_COLUMNS,
                [
                    {
                        "id_speech": "M_5",
                        "extract_id": "M_5_gender_equality_position",
                        "cluster": "Gender and women's leadership",
                        "indicator_name": "gender_equality_position",
                        "code": "3",
                        "option": "Negative / Pushback",
                        "textual_extract": "must not overwrite",
                        "coder_notes": "",
                        "coding_source": "claude",
                        "model": "claude-haiku-4-5",
                        "date_coded": "2026-09-15",
                    },
                    {
                        "id_speech": "M_1",
                        "extract_id": "M_1_gender_equality_position",
                        "cluster": "Gender and women's leadership",
                        "indicator_name": "gender_equality_position",
                        "code": "0",
                        "option": "No Mention",
                        "textual_extract": "",
                        "coder_notes": "",
                        "coding_source": "claude",
                        "model": "claude-haiku-4-5",
                        "date_coded": "2026-09-15",
                    },
                    {
                        "id_speech": "M_2",
                        "extract_id": "M_2_un_reform_position",
                        "cluster": "UN reform",
                        "indicator_name": "un_reform_position",
                        "code": "1",
                        "option": "Positive / Supportive",
                        "textual_extract": "Reform the UN.",
                        "coder_notes": "",
                        "coding_source": "claude",
                        "model": "claude-haiku-4-5",
                        "date_coded": "2026-09-15",
                    },
                ],
            )
            _write_csv(
                directory / "Emerging_Priorities.csv",
                EMERGING_COLUMNS,
                [
                    {
                        "id_speech": "M_1",
                        "id_extract": "M_1_climate_change",
                        "emerging_topic": "Climate change",
                        "textual_extract": "Climate crisis.",
                    },
                    {
                        "id_speech": "M_2",
                        "id_extract": "M_2_peace_and_security",
                        "emerging_topic": "Peace and security",
                        "textual_extract": "Preserve peace.",
                    },
                ],
            )
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                rc = publish_coding_day(
                    config,
                    day="2025-09-23",
                    dest=dest,
                    settings=settings,
                    worksheets={
                        "Indicators": indicators_ws,
                        "Emerging_Priorities": emerging_ws,
                    },
                )
            self.assertEqual(rc, 0)
            log = stderr.getvalue()
            self.assertIn("SHEET skip Indicators M_5_gender_equality_position", log)
            summary = json.loads(log.strip().splitlines()[-1])
            self.assertEqual(summary["indicators"], {"append": 2, "skip": 1})
            self.assertEqual(summary["emerging"], {"append": 2, "skip": 0})
            self.assertFalse(summary["dry_run"])

            headers = indicators_ws.updates[0][1][0]
            self.assertEqual(headers[-2:], ["date", "country"])
            self.assertNotIn("coding_source", headers)
            idx = {name: i for i, name in enumerate(headers)}
            by_id = {row[idx["extract_id"]]: row for row in indicators_ws.appends[0]}
            self.assertNotIn("M_5_gender_equality_position", by_id)
            self.assertEqual(by_id["M_1_gender_equality_position"][idx["date"]], "2025-09-23")
            self.assertEqual(by_id["M_1_gender_equality_position"][idx["country"]], "Angola")
            self.assertEqual(by_id["M_2_un_reform_position"][idx["country"]], "Brazil")
            self.assertEqual(indicators_ws._values[1][4], "1")

            em_headers = emerging_ws.updates[0][1][0]
            em_idx = {name: i for i, name in enumerate(em_headers)}
            em_by_id = {row[em_idx["id_extract"]]: row for row in emerging_ws.appends[0]}
            self.assertEqual(em_by_id["M_1_climate_change"][em_idx["country"]], "Angola")
            self.assertEqual(em_by_id["M_2_peace_and_security"][em_idx["date"]], "2025-09-23")

    def test_dry_run_cli_does_not_append(self) -> None:
        config = load_session("80")
        settings = _settings()
        indicators_ws = FakeWorksheet(
            [
                list(INDICATORS_SHEET_COLUMNS),
                [
                    "M_5",
                    "M_5_gender_equality_position",
                    "Gender and women's leadership",
                    "gender_equality_position",
                    "1",
                    "Positive / Supportive",
                    "Already on sheet",
                    "",
                    "2025-09-24",
                ],
            ]
        )
        emerging_ws = FakeWorksheet([list(EMERGING_SHEET_COLUMNS)])
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            directory = out_dir(config, "2025-09-23", dest=dest)
            write_speech(_speech(), directory)
            _write_csv(
                directory / "Indicators.csv",
                INDICATOR_COLUMNS,
                [
                    {
                        "id_speech": "M_1",
                        "extract_id": "M_1_gender_equality_position",
                        "cluster": "Gender and women's leadership",
                        "indicator_name": "gender_equality_position",
                        "code": "0",
                        "option": "No Mention",
                        "textual_extract": "",
                        "coder_notes": "",
                        "coding_source": "claude",
                        "model": "claude-haiku-4-5",
                        "date_coded": "2026-09-15",
                    }
                ],
            )
            _write_csv(
                directory / "Emerging_Priorities.csv",
                EMERGING_COLUMNS,
                [
                    {
                        "id_speech": "M_1",
                        "id_extract": "M_1_climate_change",
                        "emerging_topic": "Climate change",
                        "textual_extract": "Climate crisis.",
                    }
                ],
            )
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                rc = publish_coding_day(
                    config,
                    day="2025-09-23",
                    dest=dest,
                    dry_run=True,
                    settings=settings,
                    worksheets={
                        "Indicators": indicators_ws,
                        "Emerging_Priorities": emerging_ws,
                    },
                )
            self.assertEqual(rc, 0)
            summary = json.loads(stderr.getvalue().strip().splitlines()[-1])
            self.assertTrue(summary["dry_run"])
            self.assertEqual(summary["indicators"]["append"], 1)
            self.assertEqual(indicators_ws.appends, [])
            self.assertEqual(emerging_ws.appends, [])
            self.assertEqual(indicators_ws.updates, [])
            self.assertEqual(emerging_ws.updates, [])

    def test_cli_wires_coding_sheet(self) -> None:
        from unittest.mock import patch

        with patch("pipeline.coding_sheet.publish_coding_day", return_value=0) as mocked:
            rc = main(
                [
                    "coding-sheet",
                    "--session",
                    "80",
                    "--day",
                    "2025-09-23",
                    "--dry-run",
                    "--out",
                    "/tmp/coding-sheet-test",
                ]
            )
        self.assertEqual(rc, 0)
        mocked.assert_called_once()
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["day"], "2025-09-23")
        self.assertTrue(kwargs["dry_run"])
        self.assertEqual(kwargs["dest"], Path("/tmp/coding-sheet-test"))


if __name__ == "__main__":
    unittest.main()

