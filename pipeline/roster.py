from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pipeline.cascade import SourceUnavailable, choose_source
from pipeline.config import SessionConfig, coerce_debate_day
from pipeline.extract_video import kaltura_play_url
from pipeline.gadebate import ListingSpeaker, fetch_homepage_listings, scrape_speaker
from pipeline.journal import write_journal_slugs
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
        "transcript_ai": _dump_ref(page.transcript_ai),
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
        transcript_ai=_load_ref(sources.get("transcript_ai")),
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


def _export_title(speaker: dict) -> str:
    rank = str(speaker.get("rank") or "").strip()
    if rank:
        return rank
    title = str(speaker.get("speaker_title") or "").strip()
    if title.casefold() in {"his excellency", "her excellency"}:
        return ""
    return title


def _leaked_names(speakers: list[dict]) -> set[str]:
    """Un mismo 'nombre' en muchos países = basura de ficha (widget de gadebate)."""
    countries_by_name: dict[str, set[str]] = {}
    for speaker in speakers:
        name = str(speaker.get("name") or "").strip()
        if not name:
            continue
        country = str(speaker.get("country") or "").strip().casefold()
        countries_by_name.setdefault(name.casefold(), set()).add(country)
    return {
        name
        for name, countries in countries_by_name.items()
        if len([item for item in countries if item]) >= 3
    }


def export_speaker_names(payload: dict, path: Path) -> Path:
    """Escribe `nombre | país | cargo` para SPEAKER_ROSTER_FILE del monitor en vivo."""
    speakers = list(payload.get("speakers") or [])
    leaked = _leaked_names(speakers)
    lines: list[str] = []
    seen: set[str] = set()
    for speaker in speakers:
        country = str(speaker.get("country") or "").strip()
        name = str(speaker.get("name") or "").strip()
        if name.casefold() in leaked:
            name = ""
        if not name:
            name = country
        if not name:
            continue
        key = f"{name.casefold()}|{country.casefold()}"
        if key in seen:
            continue
        seen.add(key)
        title = _export_title(speaker)
        if country or title:
            lines.append(f"{name} | {country} | {title}".rstrip(" |"))
        else:
            lines.append(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "# Auto-exportado desde roster UNGA para el monitor de alertas\n"
    body += "# nombre | país | cargo\n"
    body += "\n".join(lines)
    if lines:
        body += "\n"
    path.write_text(body, encoding="utf-8")
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


def _generic_country(value: str) -> bool:
    text = (value or "").strip().casefold()
    return not text or text in {"general debate", "united nations"}


def _apply_listing(page: SpeakerPage, listing: ListingSpeaker | None, day: str) -> None:
    if listing is None:
        return
    if listing.name:
        page.name = listing.name
    if listing.title and _generic_country(page.country):
        page.country = listing.title
    if not page.speech_date:
        page.speech_date = day
    if page.error and (
        str(page.error).startswith("ficha vacía") or page.http_status == 404
    ) and (page.name or listing.name):
        page.error = None


def build_roster(
    config: SessionConfig,
    *,
    day: str,
    slug: str | None = None,
    limit: int | None = None,
) -> dict:
    from pipeline.run import select_slugs

    day = coerce_debate_day(config, day)
    listings: list[ListingSpeaker] = []
    slugs = select_slugs(config, day=day, slug=slug, limit=limit)
    if not slug:
        try:
            listings = fetch_homepage_listings(config, day)
        except Exception:
            if not slugs:
                raise
            listings = []
        if listings:
            listed = [item.slug for item in listings]
            if not slugs:
                slugs = listed
                write_journal_slugs(
                    config,
                    day,
                    slugs,
                    parts=[item.part for item in listings],
                )
            else:
                extra = [item for item in listed if item not in slugs]
                if extra:
                    slugs = slugs + extra
                    part_of = {item.slug: item.part for item in listings}
                    write_journal_slugs(
                        config,
                        day,
                        slugs,
                        parts=[part_of.get(item, "") for item in slugs],
                        overwrite=True,
                    )
        if limit is not None:
            slugs = slugs[:limit]
    by_slug = {item.slug: item for item in listings}
    speakers: list[dict] = []
    for item in slugs:
        page = scrape_speaker(config, item)
        _apply_listing(page, by_slug.get(item), day)
        if day and page.speech_date and page.speech_date != day:
            continue
        speakers.append(speaker_entry_from_page(page, config.sources))
    return {
        "session": config.id,
        "day": day,
        "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "speakers": speakers,
    }
