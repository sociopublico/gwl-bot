from __future__ import annotations

import json
import re

from urllib.parse import urljoin

from pipeline.cascade import SourceUnavailable
from pipeline.config import SessionConfig
from pipeline.extract_pdf import strip_assembly_protocol
from pipeline.http import HttpError, fetch, is_waf_challenge
from pipeline.models import FileRef

_HEADER_LINE = re.compile(
    r"^(?:Meeting/Event:|Date:|Categories:|\s*\[Auto-generated transcript)",
    re.I,
)


def clean_ai_transcript(text: str) -> str:
    lines = [
        line
        for line in (text or "").splitlines()
        if not _HEADER_LINE.match(line.strip())
    ]
    body = "\n".join(lines).strip()
    hear = re.search(
        r"(?:the\s+)?assembly\s+will\s+(?:now\s+)?hear\b",
        body,
        re.I,
    )
    if hear:
        body = body[hear.start() :]
    return strip_assembly_protocol(body)


def download_ai_transcript(config: SessionConfig, ref: FileRef) -> str:
    try:
        _, _, body = fetch(
            ref.url,
            user_agent=config.user_agent,
            method="POST",
            extra_headers={
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
    except HttpError as exc:
        raise SourceUnavailable(
            f"transcript_ai ({ref.filename}): HTTP {exc.status or '?'} {exc}"
        ) from exc
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise SourceUnavailable(
            f"transcript_ai ({ref.filename}): prepare-download no devolvió JSON"
        ) from exc
    rel = str(payload.get("url") or "").strip()
    if not rel:
        raise SourceUnavailable(
            f"transcript_ai ({ref.filename}): prepare-download sin url"
        )
    url = urljoin(ref.url, rel)
    try:
        _, _, blob = fetch(url, user_agent=config.user_agent)
    except HttpError as exc:
        raise SourceUnavailable(
            f"transcript_ai ({ref.filename}): HTTP {exc.status or '?'} {exc}"
        ) from exc
    if is_waf_challenge(blob) or blob.lstrip().lower().startswith(
        (b"<!doctype", b"<html")
    ):
        raise SourceUnavailable(
            f"transcript_ai ({ref.filename}): HTML/WAF en vez del txt; sigo la cascada"
        )
    return blob.decode("utf-8", "replace")
