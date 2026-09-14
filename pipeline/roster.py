from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pipeline.cascade import SourceUnavailable, choose_source
from pipeline.config import SessionConfig
from pipeline.extract_video import kaltura_play_url
from pipeline.gadebate import scrape_speaker
from pipeline.models import FileRef, SpeakerPage


def roster_path(config: SessionConfig, day: str) -> Path:
    return config.root / "data" / "roster" / str(config.id) / f"{day}.json"


def _dump_ref(ref: FileRef | None) -> dict | None:
    if ref is None:
        return None
    return {
        "label": ref.label,
        "url": ref.url,
        "filename": ref.filename,
        "lang": ref.lang,
    }


def _load_ref(raw: dict | None) -> FileRef | None:
    if not raw:
        return None
    return FileRef(
        label=str(raw.get("label") or ""),
        url=str(raw.get("url") or ""),
        filename=str(raw.get("filename") or ""),
        lang=str(raw.get("lang") or ""),
    )


def sources_from_page(page: SpeakerPage) -> dict:
    video = None
    if page.video_entry_id:
        video = {
            "entry_id": page.video_entry_id,
            "partner_id": page.video_partner_id or "",
            "url": kaltura_play_url(page.video_entry_id, page.video_partner_id),
            "filename": f"{page.video_entry_id}.mp4",
        }
    return {
        "pdf_en": _dump_ref(page.pdf_en),
        "audio_en": _dump_ref(page.audio_en),
        "pdf_other": _dump_ref(page.pdf_other),
        "audio_floor": _dump_ref(page.audio_floor),
        "video": video,
    }


def chosen_source(page: SpeakerPage, order: tuple[str, ...]) -> str | None:
    if page.error:
        return None
    try:
        source, _ = choose_source(page, order)
        return source
    except SourceUnavailable:
        return None


def speaker_entry_from_page(page: SpeakerPage, order: tuple[str, ...]) -> dict:
    return {
        "slug": page.slug,
        "ficha_url": page.url,
        "country": page.country,
        "name": page.name,
        "rank": page.rank,
        "speaker_title": page.speaker_title,
        "speech_date": page.speech_date,
        "chosen": chosen_source(page, order),
        "sources": sources_from_page(page),
        "error": page.error,
        "http_status": page.http_status,
    }


def page_from_entry(entry: dict) -> SpeakerPage:
    sources = entry.get("sources") or {}
    video = sources.get("video") or {}
    return SpeakerPage(
        slug=str(entry.get("slug") or ""),
        url=str(entry.get("ficha_url") or ""),
        country=str(entry.get("country") or ""),
        name=str(entry.get("name") or ""),
        rank=str(entry.get("rank") or ""),
        speaker_title=str(entry.get("speaker_title") or ""),
        speech_date=str(entry.get("speech_date") or ""),
        pdf_en=_load_ref(sources.get("pdf_en")),
        audio_en=_load_ref(sources.get("audio_en")),
        pdf_other=_load_ref(sources.get("pdf_other")),
        audio_floor=_load_ref(sources.get("audio_floor")),
        video_entry_id=(video.get("entry_id") or None) if video else None,
        video_partner_id=(video.get("partner_id") or None) if video else None,
        error=entry.get("error"),
        http_status=entry.get("http_status"),
    )


def load_roster(config: SessionConfig, day: str) -> dict | None:
    path = roster_path(config, day)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_roster(payload: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def merge_speakers(existing: dict | None, incoming: dict) -> dict:
    """Reemplaza o agrega oradores por slug; conserva el orden del roster previo."""
    if not existing or not existing.get("speakers"):
        return incoming
    by_slug: dict[str, dict] = {}
    order: list[str] = []
    for speaker in existing.get("speakers") or []:
        slug = str(speaker.get("slug") or "")
        if not slug:
            continue
        by_slug[slug] = speaker
        order.append(slug)
    for speaker in incoming.get("speakers") or []:
        slug = str(speaker.get("slug") or "")
        if not slug:
            continue
        by_slug[slug] = speaker
        if slug not in order:
            order.append(slug)
    return {
        **incoming,
        "speakers": [by_slug[slug] for slug in order if slug in by_slug],
    }


def build_roster(
    config: SessionConfig,
    *,
    day: str,
    slug: str | None = None,
    limit: int | None = None,
) -> dict:
    from pipeline.run import select_slugs

    slugs = select_slugs(config, day=day, slug=slug, limit=limit)
    speakers: list[dict] = []
    for item in slugs:
        page = scrape_speaker(config, item)
        if day and page.speech_date and page.speech_date != day:
            continue
        speakers.append(speaker_entry_from_page(page, config.sources))
    return {
        "session": config.id,
        "day": day,
        "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "speakers": speakers,
    }
