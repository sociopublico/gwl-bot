"""Paso 6: CSV de coding del día → pestañas Indicators y Emerging_Priorities."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from pipeline.coding import emerging_csv_path, indicators_csv_path, speech_id_of
from pipeline.config import SessionConfig
from pipeline.sheets import (
    CODING_EXTRA_COLUMNS,
    EMERGING_SHEET_COLUMNS,
    INDICATORS_SHEET_COLUMNS,
    SheetsError,
    SheetsSettings,
    append_coding_rows,
    can_write_sheets,
    sheets_settings,
    sheets_unavailable_reason,
)
from pipeline.store import list_speech_txts, out_dir, parse_speech_txt


def load_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return [{key: value or "" for key, value in row.items()} for row in csv.DictReader(fh)]


def speech_date_country_lookup(
    config: SessionConfig,
    *,
    day: str,
    dest: Path | None = None,
) -> dict[str, tuple[str, str]]:
    lookup: dict[str, tuple[str, str]] = {}
    for path in list_speech_txts(config, speech_date=day, dest=dest):
        try:
            speech = parse_speech_txt(path)
        except ValueError as exc:
            print(f"SKIP {path.name}: {exc}", file=sys.stderr)
            continue
        speech_id = speech_id_of(speech, path)
        if not speech_id:
            continue
        lookup[speech_id] = (speech.speech_date or day, speech.country)
    return lookup


def merge_date_country(
    rows: list[dict[str, str]],
    lookup: dict[str, tuple[str, str]],
    *,
    day: str,
    links: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    warned: set[str] = set()
    link_map = links or {}
    for row in rows:
        speech_id = str(row.get("id_speech") or "").strip()
        date, country = lookup.get(speech_id, ("", ""))
        if speech_id and speech_id not in lookup and speech_id not in warned:
            print(
                f"WARN {speech_id}: sin .txt del día; date={day} country vacío",
                file=sys.stderr,
            )
            warned.add(speech_id)
        out = dict(row)
        out["date"] = date or day
        out["country"] = country
        if speech_id in link_map:
            out["link"] = link_map[speech_id]
        merged.append(out)
    return merged


def speech_link_lookup(
    config: SessionConfig,
    *,
    day: str,
    dest: Path | None = None,
) -> dict[str, str]:
    from pipeline.publish import transcript_url_for
    from pipeline.store import speech_relpath
    from pipeline.config import PIPELINE_ROOT

    repo_root = PIPELINE_ROOT.parent
    links: dict[str, str] = {}
    for path in list_speech_txts(config, speech_date=day, dest=dest):
        try:
            speech = parse_speech_txt(path)
        except ValueError:
            continue
        speech_id = speech_id_of(speech, path)
        if not speech_id:
            continue
        rel = speech_relpath(path, repo_root=repo_root)
        url = transcript_url_for(rel)
        if url:
            links[speech_id] = url
    return links


def _tab_summary(written: list[dict[str, str]], skipped: list[dict[str, str]]) -> dict[str, int]:
    return {"append": len(written), "skip": len(skipped)}


def publish_coding_day(
    config: SessionConfig,
    *,
    day: str,
    dest: Path | None = None,
    dry_run: bool = False,
    settings: SheetsSettings | None = None,
    worksheets: dict[str, object] | None = None,
) -> int:
    directory = out_dir(config, day, dest=dest)
    indicators_path = indicators_csv_path(directory)
    emerging_path = emerging_csv_path(directory)
    missing = [p.name for p in (indicators_path, emerging_path) if not p.is_file()]
    if missing:
        print(
            f"error: faltan {', '.join(missing)} en {directory}; corré coding primero",
            file=sys.stderr,
        )
        return 1

    cfg = settings or sheets_settings()
    if worksheets is None and not can_write_sheets(cfg):
        print(f"error: {sheets_unavailable_reason(cfg)}", file=sys.stderr)
        return 1

    lookup = speech_date_country_lookup(config, day=day, dest=dest)
    links = speech_link_lookup(config, day=day, dest=dest)
    indicator_rows = merge_date_country(
        load_csv_rows(indicators_path), lookup, day=day, links=links
    )
    emerging_rows = merge_date_country(
        load_csv_rows(emerging_path), lookup, day=day, links=links
    )
    extra = CODING_EXTRA_COLUMNS

    print(
        f"coding-sheet session={config.id} day={day} "
        f"indicators={len(indicator_rows)} emerging={len(emerging_rows)} "
        f"dry_run={int(dry_run)}",
        file=sys.stderr,
    )
    try:
        ind_written, ind_skipped = append_coding_rows(
            indicator_rows,
            tab=cfg.indicators_tab,
            key_column="extract_id",
            extra_columns=extra,
            default_headers=list(INDICATORS_SHEET_COLUMNS),
            settings=cfg,
            ws=None if worksheets is None else worksheets.get(cfg.indicators_tab),
            dry_run=dry_run,
        )
        em_written, em_skipped = append_coding_rows(
            emerging_rows,
            tab=cfg.emerging_tab,
            key_column="id_extract",
            extra_columns=extra,
            default_headers=list(EMERGING_SHEET_COLUMNS),
            settings=cfg,
            ws=None if worksheets is None else worksheets.get(cfg.emerging_tab),
            dry_run=dry_run,
        )
    except SheetsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "day": day,
                "indicators": _tab_summary(ind_written, ind_skipped),
                "emerging": _tab_summary(em_written, em_skipped),
                "dry_run": dry_run,
            }
        ),
        file=sys.stderr,
    )
    return 0
