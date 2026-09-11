"""Cliente Google Sheets (gspread + service account).

Setup (una vez):
1. En el Sheet de edición copiar el ID de /d/{SPREADSHEET_ID}/edit
   (el link 2PACX/pub?output=csv solo sirve para leer CSV, no para escribir).
2. GCP: habilitar Google Sheets API, crear service account, bajar JSON.
3. Compartir el spreadsheet con el email de la SA como Editor.
4. Variables: GOOGLE_SHEETS_SPREADSHEET_ID, GOOGLE_APPLICATION_CREDENTIALS,
   pestañas Metadata y country_list.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from pipeline.env import load_dotenv
from pipeline.metadata import METADATA_COLUMNS, MetadataRow, row_values


@dataclass(frozen=True)
class SheetsSettings:
    spreadsheet_id: str
    metadata_tab: str
    country_tab: str
    credentials_path: str
    country_csv: str


class SheetsError(RuntimeError):
    pass


def sheets_settings() -> SheetsSettings:
    load_dotenv()
    return SheetsSettings(
        spreadsheet_id=os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID", "").strip(),
        metadata_tab=os.environ.get("GOOGLE_SHEETS_METADATA_TAB", "Metadata").strip()
        or "Metadata",
        country_tab=os.environ.get("GOOGLE_SHEETS_COUNTRY_TAB", "country_list").strip()
        or "country_list",
        credentials_path=os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip(),
        country_csv=os.environ.get("GOOGLE_SHEETS_COUNTRY_CSV", "").strip(),
    )


def can_write_sheets(settings: SheetsSettings | None = None) -> bool:
    cfg = settings or sheets_settings()
    return bool(cfg.spreadsheet_id and cfg.credentials_path and Path(cfg.credentials_path).is_file())


def sheets_unavailable_reason(settings: SheetsSettings | None = None) -> str:
    cfg = settings or sheets_settings()
    if not cfg.spreadsheet_id:
        return (
            "falta GOOGLE_SHEETS_SPREADSHEET_ID en .env "
            "(el ID de /d/{ID}/edit, no el link 2PACX)"
        )
    if not cfg.credentials_path:
        return "falta GOOGLE_APPLICATION_CREDENTIALS en .env (path al JSON de la service account)"
    if not Path(cfg.credentials_path).is_file():
        return (
            f"no encuentro el JSON en {cfg.credentials_path} "
            "(GOOGLE_APPLICATION_CREDENTIALS)"
        )
    return ""


def _client(settings: SheetsSettings):
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError as exc:
        raise SheetsError(
            "faltan gspread/google-auth. "
            "Instalalos con: pipeline/.venv/bin/pip install -r pipeline/requirements.txt"
        ) from exc
    if not settings.spreadsheet_id:
        raise SheetsError(
            "falta GOOGLE_SHEETS_SPREADSHEET_ID "
            "(el ID de https://docs.google.com/spreadsheets/d/{ID}/edit, no el link 2PACX)"
        )
    path = Path(settings.credentials_path)
    if not path.is_file():
        raise SheetsError(
            "falta GOOGLE_APPLICATION_CREDENTIALS apuntando al JSON de la service account"
        )
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(str(path), scopes=scopes)
    return gspread.authorize(creds)


def _open_worksheet(settings: SheetsSettings, tab: str):
    gc = _client(settings)
    try:
        sh = gc.open_by_key(settings.spreadsheet_id)
    except Exception as exc:  # noqa: BLE001
        raise SheetsError(
            f"no pude abrir el spreadsheet {settings.spreadsheet_id}: {exc}"
        ) from exc
    try:
        return sh.worksheet(tab)
    except Exception as exc:  # noqa: BLE001
        raise SheetsError(f"no encontré la pestaña {tab!r}: {exc}") from exc


def read_country_records(settings: SheetsSettings | None = None) -> list[dict]:
    cfg = settings or sheets_settings()
    ws = _open_worksheet(cfg, cfg.country_tab)
    return ws.get_all_records()


def read_metadata_records(settings: SheetsSettings | None = None) -> tuple[list[str], list[dict]]:
    cfg = settings or sheets_settings()
    ws = _open_worksheet(cfg, cfg.metadata_tab)
    values = ws.get_all_values()
    if not values:
        return list(METADATA_COLUMNS), []
    headers = [str(h).strip() for h in values[0]]
    records: list[dict] = []
    for row in values[1:]:
        record = {
            headers[i]: (row[i] if i < len(row) else "")
            for i in range(len(headers))
        }
        if any(str(v).strip() for v in record.values()):
            records.append(record)
    return headers, records


def existing_keys(records: list[dict]) -> set[str]:
    keys: set[str] = set()
    for record in records:
        ficha = str(record.get("ficha_url") or "").strip()
        date_time = str(record.get("date_time") or "").strip()
        if ficha:
            keys.add(ficha)
            tail = ficha.rstrip("/").rsplit("/", 1)[-1]
            if tail and date_time:
                keys.add(f"{tail}|{date_time}")
    return keys


def append_metadata_rows(
    rows: list[MetadataRow],
    *,
    settings: SheetsSettings | None = None,
    existing: list[dict] | None = None,
    headers: list[str] | None = None,
) -> list[MetadataRow]:
    """Append idempotente por ficha_url (o slug+fecha). Devuelve las filas nuevas."""
    cfg = settings or sheets_settings()
    ws = _open_worksheet(cfg, cfg.metadata_tab)
    if headers is None or existing is None:
        headers, existing = read_metadata_records(cfg)
    if not headers:
        headers = list(METADATA_COLUMNS)
        ws.update("A1", [headers])
    needed = {"id_speech", "ficha_url", "source", "original language", "transformation"}
    missing = needed - set(headers)
    if missing:
        print(
            f"sheet: la pestaña Metadata no tiene columnas {sorted(missing)}; "
            "revisá el encabezado",
            file=sys.stderr,
        )
    seen = existing_keys(existing)
    written: list[MetadataRow] = []
    payload: list[list[str]] = []
    for row in rows:
        ficha = row.ficha_url.strip()
        slug_date = f"{row.slug}|{row.date_time}"
        if (ficha and ficha in seen) or slug_date in seen:
            print(f"SHEET skip duplicado {row.slug} {row.date_time}", file=sys.stderr)
            continue
        payload.append(row_values(row, headers))
        written.append(row)
        if ficha:
            seen.add(ficha)
        seen.add(slug_date)
    if payload:
        ws.append_rows(payload, value_input_option="USER_ENTERED")
    return written
