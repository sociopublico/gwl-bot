from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def video_id_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""

    if host in {"youtu.be", "www.youtu.be"}:
        vid = path.lstrip("/").split("/")[0]
        return vid if YOUTUBE_ID_RE.match(vid) else None

    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"} or host.endswith(
        ".youtube.com"
    ):
        if path.startswith("/watch"):
            vid = (parse_qs(parsed.query).get("v") or [None])[0]
            return vid if vid and YOUTUBE_ID_RE.match(vid) else None
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 2 and parts[0] in {"live", "embed", "shorts", "v"}:
            vid = parts[1]
            return vid if YOUTUBE_ID_RE.match(vid) else None
        if len(parts) == 1 and YOUTUBE_ID_RE.match(parts[0]):
            return parts[0]
    return None


def watch_url_at(video_id: str, seconds: float) -> str:
    """Link que busca el segundo. En un live, embed+start pisa el salto al vivo."""
    t = max(0, int(round(seconds)))
    return f"https://www.youtube.com/embed/{video_id}?start={t}"


def watch_page_url_at(video_id: str, seconds: float) -> str:
    t = max(0, int(round(seconds)))
    return f"https://www.youtube.com/watch?v={video_id}&t={t}"


def format_timecode(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
