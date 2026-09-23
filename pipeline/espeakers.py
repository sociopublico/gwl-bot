from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from pipeline.http import fetch

ESPEAKERS_BASE = "https://e-speakers.e-delegate.un.org"

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

_DATE_IN_TITLE = re.compile(
    r"(\d{1,2})\s+(January|February|March|April|May|June|July|"
    r"August|September|October|November|December)\s+(\d{4})",
    re.I,
)
_HASH = re.compile(r"^[0-9a-f]{16,}$", re.I)


@dataclass(frozen=True)
class ListedSpeaker:
    name: str
    country: str
    title: str
    day: str
    meeting: str
    slot: int


def data_url(raw: str) -> str:
    """Acepta hash, URL de la página o URL de data.json."""
    value = raw.strip()
    if not value:
        raise ValueError("url de e-speakers vacía")
    if _HASH.fullmatch(value):
        return f"{ESPEAKERS_BASE}/{value}/data.json"
    parsed = urlparse(value)
    if not parsed.scheme:
        raise ValueError(f"url de e-speakers inválida: {raw!r}")
    path = parsed.path.rstrip("/")
    if path.endswith("/data.json"):
        return f"{parsed.scheme}://{parsed.netloc}{path}"
    if not path:
        raise ValueError(f"url de e-speakers sin id de lista: {raw!r}")
    return f"{parsed.scheme}://{parsed.netloc}{path}/data.json"


def page_url(raw: str) -> str:
    json_url = data_url(raw)
    if json_url.endswith("/data.json"):
        return json_url[: -len("/data.json")]
    return json_url


def meeting_day(meeting: dict) -> str:
    item = meeting.get("MT_agendaitem") or {}
    title = str(item.get("MT_AG_title") or "")
    match = _DATE_IN_TITLE.search(title)
    if not match:
        return ""
    day_n, month, year = match.groups()
    return f"{int(year):04d}-{_MONTHS[month.casefold()]:02d}-{int(day_n):02d}"


def meeting_label(meeting: dict) -> str:
    item = meeting.get("MT_agendaitem") or {}
    return str(item.get("MT_AG_item") or "").strip()


def _country(raw: dict) -> str:
    entity = raw.get("SP_entity") or {}
    if not isinstance(entity, dict):
        return ""
    return str(entity.get("SP_entityShort") or entity.get("SP_entity") or "").strip()


def _is_placeholder(raw: dict) -> bool:
    entity = raw.get("SP_entity") or {}
    blob = " ".join(
        [
            str(entity.get("SP_entity") or "") if isinstance(entity, dict) else "",
            str(entity.get("SP_entityShort") or "") if isinstance(entity, dict) else "",
            str(raw.get("SP_LS_fname") or ""),
            str(raw.get("SP_LS_lname") or ""),
        ]
    )
    return "forthcoming" in blob.casefold()


def _title(raw: dict) -> str:
    return " ".join(str(raw.get("SP_LS_funcTitle") or "").split())


def speaker_name(raw: dict) -> str:
    fname = " ".join(str(raw.get("SP_LS_fname") or "").split())
    lname = " ".join(str(raw.get("SP_LS_lname") or "").split())
    if not fname and not lname:
        return ""
    if raw.get("SP_LS_nameFormat") and fname and lname:
        return f"{lname} {fname}"
    return " ".join(part for part in (fname, lname) if part)


def _slot_no(raw: dict) -> int:
    try:
        return int(str(raw.get("SP_slotNo") or "0").strip() or "0")
    except ValueError:
        return 0


def parse_speakers(payload: dict) -> list[ListedSpeaker]:
    meetings = payload.get("sampleData") or []
    speakers: list[ListedSpeaker] = []
    for meeting in meetings:
        day = meeting_day(meeting)
        label = meeting_label(meeting)
        rows = list(meeting.get("MT_speakers") or [])
        rows.sort(key=_slot_no)
        for raw in rows:
            if raw.get("SP_isSkip") or _is_placeholder(raw):
                continue
            if raw.get("SP_approval") is False:
                continue
            name = speaker_name(raw)
            if not name:
                continue
            speakers.append(
                ListedSpeaker(
                    name=name,
                    country=_country(raw),
                    title=_title(raw),
                    day=day,
                    meeting=label,
                    slot=_slot_no(raw),
                )
            )
    return speakers


def filter_day(speakers: list[ListedSpeaker], day: str | None) -> list[ListedSpeaker]:
    if not day:
        return speakers
    return [speaker for speaker in speakers if speaker.day == day]


def unique_speakers(speakers: list[ListedSpeaker]) -> list[ListedSpeaker]:
    unique: list[ListedSpeaker] = []
    seen: set[str] = set()
    for speaker in speakers:
        key = speaker.name.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(speaker)
    return unique


def unique_names(speakers: list[ListedSpeaker]) -> list[str]:
    return [speaker.name for speaker in unique_speakers(speakers)]


def available_days(speakers: list[ListedSpeaker]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    order: list[str] = []
    for speaker in speakers:
        day = speaker.day or "?"
        if day not in counts:
            counts[day] = 0
            order.append(day)
        counts[day] += 1
    return [(day, counts[day]) for day in order]


def write_speakers_txt(
    speakers: list[ListedSpeaker],
    path: Path,
    *,
    source: str,
    day: str | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    day_bit = f" --day {day}" if day else ""
    lines = [
        f"# Auto-exportado desde e-speakers{day_bit}",
        f"# {source}",
        "# nombre | país | cargo",
    ]
    for speaker in unique_speakers(speakers):
        if speaker.country or speaker.title:
            lines.append(f"{speaker.name} | {speaker.country} | {speaker.title}".rstrip(" |"))
        else:
            lines.append(speaker.name)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def fetch_payload(url: str, *, user_agent: str) -> dict:
    json_url = data_url(url)
    _, _, body = fetch(json_url, user_agent=user_agent, timeout=40)
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"e-speakers no devolvió JSON: {json_url}") from exc
    if not isinstance(payload, dict) or not payload.get("sampleData"):
        raise RuntimeError(f"e-speakers sin lista de reuniones: {json_url}")
    return payload
