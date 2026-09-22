from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.detector import DetectionEvent

_NY = ZoneInfo("America/New_York")
_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| KEYWORD_DETECTED \| (?P<body>.*)$"
)
_T_RE = re.compile(r"^t=\d+s\??$")


def event_day(stamp: datetime) -> str:
    return stamp.astimezone(_NY).strftime("%Y-%m-%d")


def event_to_record(event: DetectionEvent) -> dict:
    stamp = event.timestamp if event.timestamp.tzinfo else event.timestamp.replace(
        tzinfo=timezone.utc
    )
    speaker = (event.speaker or "").strip()
    if speaker.casefold() == "unknown":
        speaker = ""
    return {
        "timestamp": stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "day": event_day(stamp),
        "keyword": event.keyword,
        "speaker": speaker,
        "speaker_title": event.speaker_title or "",
        "context": event.context,
        "video_seconds": event.video_seconds,
        "timestamp_reliable": event.timestamp_reliable,
        "watch_url": event.watch_url or "",
        "webtv_url": event.webtv_url or "",
    }


def parse_highlights_line(line: str) -> dict | None:
    """Parsea una línea de highlights.log. Ignora SPEAKER_CHANGED / watchdog."""
    match = _LINE_RE.match(line.strip())
    if not match:
        return None
    naive = datetime.strptime(match.group("ts"), "%Y-%m-%d %H:%M:%S")
    stamp = naive.replace(tzinfo=timezone.utc)
    parts = [part.strip() for part in match.group("body").split(" | ")]
    if len(parts) < 2:
        return None
    keyword = parts[0]
    context = parts[-1]
    middle = parts[1:-1]
    speaker = ""
    video_seconds = None
    timestamp_reliable = True
    for item in middle:
        if _T_RE.match(item):
            raw = item.removeprefix("t=").removesuffix("s").rstrip("?")
            try:
                video_seconds = float(raw)
            except ValueError:
                video_seconds = None
            timestamp_reliable = not item.endswith("?")
        elif item and item.casefold() != "unknown":
            speaker = item
    return {
        "timestamp": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "day": event_day(stamp),
        "keyword": keyword,
        "speaker": speaker,
        "speaker_title": "",
        "context": context,
        "video_seconds": video_seconds,
        "timestamp_reliable": timestamp_reliable,
        "watch_url": "",
        "webtv_url": "",
    }


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    records: list[dict] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("keyword"):
            records.append(item)
    return records


def load_highlights(log_dir: Path) -> list[dict]:
    records: list[dict] = []
    for path in sorted(log_dir.glob("highlights.log*")):
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            parsed = parse_highlights_line(raw)
            if parsed:
                records.append(parsed)
    return records


def hit_key(record: dict) -> tuple:
    return (
        str(record.get("day") or ""),
        str(record.get("timestamp") or ""),
        str(record.get("keyword") or "").casefold(),
        str(record.get("context") or ""),
        str(record.get("speaker") or "").casefold(),
    )


def merge_records(*groups: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    merged: list[dict] = []
    for group in groups:
        for record in group:
            key = hit_key(record)
            if key in seen:
                continue
            seen.add(key)
            merged.append(record)
    merged.sort(key=lambda item: (item.get("timestamp") or "", item.get("keyword") or ""))
    return merged


class KeywordJournal:
    """JSONL durable de KEYWORD_DETECTED (una línea por hit)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: DetectionEvent) -> None:
        line = json.dumps(event_to_record(event), ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
