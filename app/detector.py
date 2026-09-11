from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from app.transcript import TranscriptSegment
from app.youtube import watch_url_at

_WORD_RE = re.compile(r"\S+")


@dataclass(frozen=True)
class DetectionEvent:
    timestamp: datetime
    keyword: str
    transcript: str
    context: str
    speaker: str = "unknown"
    speaker_title: str | None = None
    video_seconds: float | None = None
    watch_url: str | None = None
    timestamp_reliable: bool = True


def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    parts = [re.escape(part) for part in keyword.split() if part]
    if not parts:
        raise ValueError("keyword vacía")
    body = r"\s+".join(parts)
    return re.compile(rf"(?i)\b{body}\b")


def _snippet(text: str, match: re.Match[str], context_words: int) -> str:
    start, end = match.span()
    before = _WORD_RE.findall(text[:start])
    after = _WORD_RE.findall(text[end:])
    left = before[-context_words:] if context_words else []
    right = after[:context_words] if context_words else []
    pieces = left + [match.group(0)] + right
    body = " ".join(pieces)
    prefix = "..." if len(before) > context_words else ""
    suffix = "..." if len(after) > context_words else ""
    return f'"{prefix}{body}{suffix}"'


def _join_segments(
    segments: Sequence[TranscriptSegment],
) -> tuple[str, list[tuple[int, int, TranscriptSegment]]]:
    parts: list[str] = []
    spans: list[tuple[int, int, TranscriptSegment]] = []
    cursor = 0
    for segment in segments:
        piece = " ".join(segment.text.split())
        if not piece:
            continue
        if parts:
            cursor += 1
        start = cursor
        cursor += len(piece)
        parts.append(piece)
        spans.append((start, cursor, segment))
    return " ".join(parts), spans


def _segment_at(
    spans: Sequence[tuple[int, int, TranscriptSegment]],
    char_index: int,
) -> TranscriptSegment | None:
    if not spans:
        return None
    for start, end, segment in spans:
        if start <= char_index < end:
            return segment
    if char_index >= spans[-1][1]:
        return spans[-1][2]
    return spans[0][2]


def detect_keywords(
    transcript: str,
    keywords: tuple[str, ...],
    context_words: int,
    timestamp: datetime | None = None,
    *,
    segments: Sequence[TranscriptSegment] | None = None,
    window_start: float | None = None,
    video_id: str | None = None,
    speaker: str = "unknown",
    speaker_title: str | None = None,
    timestamp_reliable: bool = True,
) -> list[DetectionEvent]:
    spans: list[tuple[int, int, TranscriptSegment]] = []
    if segments:
        text, spans = _join_segments(segments)
    else:
        text = " ".join(transcript.split())
    if not text:
        return []

    now = timestamp or datetime.now(timezone.utc).astimezone()
    events: list[DetectionEvent] = []
    for keyword in keywords:
        pattern = _keyword_pattern(keyword)
        for match in pattern.finditer(text):
            video_seconds = window_start
            matched = _segment_at(spans, match.start())
            if matched is not None and window_start is not None:
                video_seconds = window_start + matched.start
            watch_url = None
            if video_id and video_seconds is not None:
                watch_url = watch_url_at(video_id, video_seconds)
            events.append(
                DetectionEvent(
                    timestamp=now,
                    keyword=keyword,
                    transcript=text,
                    context=_snippet(text, match, context_words),
                    speaker=speaker,
                    speaker_title=speaker_title,
                    video_seconds=video_seconds,
                    watch_url=watch_url,
                    timestamp_reliable=timestamp_reliable,
                )
            )
    return events
