from __future__ import annotations

import csv
import io
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from pipeline.config import PIPELINE_ROOT

DEFAULT_COUNTRIES_CSV = PIPELINE_ROOT / "data" / "countries.csv"

NO_ISO_SLUGS = {
    "european-union",
    "president-general-assembly-opening",
    "president-general-assembly-closing",
    "secretary-general-united-nations",
}

# Niveles de metadata para oradores institucionales (no son Estados).
INSTITUTIONAL_LEVELS = {
    "secretary-general-united-nations": "SG",
    "president-general-assembly-opening": "PGA",
    "president-general-assembly-closing": "PGA",
}

# Títulos del Daily schedule / ficha → slug gadebate (además de slugify/weak_slug).
LISTING_SLUG_ALIASES = {
    "secretary-general": "secretary-general-united-nations",
    "secretary-general-of-the-united-nations": "secretary-general-united-nations",
    "un-secretary-general": "secretary-general-united-nations",
    "president-general-assembly": "president-general-assembly-opening",
    "president-of-the-general-assembly": "president-general-assembly-opening",
    "president-of-the-general-assembly-opening": "president-general-assembly-opening",
    "president-of-the-general-assembly-closing": "president-general-assembly-closing",
}

# Pares gadebate slug ↔ slugify(nombre en country_list)
_ALIAS_PAIRS = (
    ("nauru", "naoero"),
    ("gambia-republic", "gambia"),
    ("republic-north-macedonia", "north-macedonia"),
    ("palestine-state", "state-of-palestine"),
    ("palestine-state", "state-palestine"),
    ("netherlands-kingdom", "netherlands-kingdom-of-the"),
    ("cote-divoire", "cote-d-ivoire"),
    ("cote-divoire", "cote-divoire"),
)

_STOP = {"of", "the", "and", "de", "la", "a"}
_APOS = dict.fromkeys(map(ord, "'’‘`´"), None)


@dataclass(frozen=True)
class CountryRow:
    country: str
    iso_country: str
    region: str
    subregion: str
    security_council_member: str
    g20_member: str


@dataclass
class CountryMatch:
    row: CountryRow | None
    expected_empty: bool
    warning: str = ""


def slugify(name: str) -> str:
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.translate(_APOS)
    text = re.sub(r"[()]", " ", text)
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text


def weak_slug(name: str) -> str:
    parts = [p for p in slugify(name).split("-") if p and p not in _STOP]
    return "-".join(parts)


def _cell(record: dict[str, str], *names: str) -> str:
    lower = {k.strip().lower(): v for k, v in record.items()}
    for name in names:
        value = lower.get(name.lower())
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _row_from_record(record: dict[str, str]) -> CountryRow | None:
    country = _cell(record, "country")
    iso = _cell(record, "iso_country")
    if not country:
        return None
    return CountryRow(
        country=country,
        iso_country=iso,
        region=_cell(record, "region"),
        subregion=_cell(record, "subregion", "sub_region"),
        security_council_member=_cell(
            record, "security_council_member", "security_council"
        ),
        g20_member=_cell(record, "g20_member"),
    )


class CountryIndex:
    def __init__(self, rows: list[CountryRow]) -> None:
        self.rows = rows
        self._by_key: dict[str, CountryRow] = {}
        for row in rows:
            for key in (slugify(row.country), weak_slug(row.country)):
                if key:
                    self._by_key.setdefault(key, row)
        for left, right in _ALIAS_PAIRS:
            if left in self._by_key:
                self._by_key.setdefault(right, self._by_key[left])
            if right in self._by_key:
                self._by_key.setdefault(left, self._by_key[right])

    def lookup(self, slug: str, country_name: str = "") -> CountryMatch:
        if slug in NO_ISO_SLUGS:
            return CountryMatch(row=None, expected_empty=True)
        for key in (
            slug,
            slugify(slug),
            weak_slug(slug),
            slugify(country_name),
            weak_slug(country_name),
        ):
            if key and key in self._by_key:
                return CountryMatch(row=self._by_key[key], expected_empty=False)
        warning = f"{slug}: sin match en country_list (iso_country vacío)"
        return CountryMatch(row=None, expected_empty=False, warning=warning)


def parse_countries_csv(text: str) -> CountryIndex:
    reader = csv.DictReader(io.StringIO(text))
    rows: list[CountryRow] = []
    for record in reader:
        row = _row_from_record({k or "": v or "" for k, v in record.items()})
        if row:
            rows.append(row)
    return CountryIndex(rows)


def load_countries_file(path: Path | None = None) -> CountryIndex:
    csv_path = path or DEFAULT_COUNTRIES_CSV
    text = csv_path.read_text(encoding="utf-8-sig")
    return parse_countries_csv(text)


def load_countries_url(url: str, *, user_agent: str) -> CountryIndex:
    from pipeline.http import fetch

    _, _, body = fetch(url, user_agent=user_agent, timeout=40)
    return parse_countries_csv(body.decode("utf-8-sig"))


def load_countries_records(records: list[dict]) -> CountryIndex:
    rows: list[CountryRow] = []
    for record in records:
        row = _row_from_record({str(k): str(v) if v is not None else "" for k, v in record.items()})
        if row:
            rows.append(row)
    return CountryIndex(rows)


def resolve_countries(
    *,
    csv_url: str = "",
    records: list[dict] | None = None,
    path: Path | None = None,
    user_agent: str = "gwl-pipeline/0.1",
) -> CountryIndex:
    if records:
        return load_countries_records(records)
    if csv_url:
        try:
            return load_countries_url(csv_url, user_agent=user_agent)
        except Exception as exc:  # noqa: BLE001 — fallback local
            print(f"country_list CSV publicado falló ({exc}); uso archivo local", file=sys.stderr)
    return load_countries_file(path)
