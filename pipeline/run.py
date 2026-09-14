from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from pipeline.cascade import SourceUnavailable
from pipeline.config import SessionConfig, load_date_index, load_slugs
from pipeline.extract_audio import transcribe_audio_file
from pipeline.extract_ocr import ocr_pdf_text, tesseract_lang_for
from pipeline.extract_pdf import extract_pdf_text
from pipeline.gadebate import scrape_speaker
from pipeline.http import HttpError, fetch, is_waf_challenge
from pipeline.journal import load_journal_slugs
from pipeline.metadata import transformation_for
from pipeline.models import ExtractedSpeech, FileRef, SpeakerPage
from pipeline.store import (
    append_manifest,
    find_existing_speech,
    is_english_transcript,
    out_dir,
    write_speech,
)

_LANG_IN_NAME = re.compile(r"_([a-z]{2})(?:\.pdf|\.mp3)$", re.I)


def language_for(source: str, ref: FileRef) -> str:
    if source in {"pdf_en", "audio_en"}:
        return "en"
    if ref.lang:
        return ref.lang
    match = _LANG_IN_NAME.search(ref.filename)
    if match:
        code = match.group(1).lower()
        if code != "fl":
            return code
    return "und"


def original_language_for(page: SpeakerPage) -> str:
    """Idioma de sala (pdf_other / as-delivered / floor), no el del archivo analizado."""
    if page.pdf_other:
        return language_for("pdf_other", page.pdf_other)
    if page.audio_floor and page.audio_floor.lang:
        return page.audio_floor.lang
    if page.pdf_en or page.audio_en:
        return "en"
    return "und"


def _speech(
    config: SessionConfig,
    page: SpeakerPage,
    *,
    source: str,
    source_url: str,
    language: str,
    text: str,
    transformation: str | None = None,
) -> ExtractedSpeech:
    original = original_language_for(page)
    return ExtractedSpeech(
        session_id=config.id,
        slug=page.slug,
        country=page.country,
        name=page.name,
        rank=page.rank,
        speech_date=page.speech_date,
        source=source,
        source_url=source_url,
        language=language,
        text=text,
        speaker_title=page.speaker_title,
        original_language=original,
        transformation=transformation or transformation_for(source),
    )


@dataclass
class FetchItem:
    page: SpeakerPage
    speech: ExtractedSpeech | None = None
    skip: str | None = None
    via: str = ""
    elapsed_s: float = 0.0


def select_slugs(
    config: SessionConfig,
    *,
    day: str | None = None,
    slug: str | None = None,
    limit: int | None = None,
) -> list[str]:
    if slug:
        slugs = [slug]
    elif day:
        journal = load_journal_slugs(config, day)
        if journal:
            slugs = journal
        else:
            slugs = load_slugs(config)
            index = load_date_index(config)
            if index:
                slugs = [s for s in slugs if index.get(s) == day]
    else:
        slugs = load_slugs(config)
    if limit is not None:
        slugs = slugs[:limit]
    return slugs


def _asset_labels(page: SpeakerPage) -> str:
    assets: list[str] = []
    if page.pdf_en:
        assets.append("pdf_en")
    if page.audio_en:
        assets.append("audio_en")
    if page.pdf_other:
        assets.append("pdf_other")
    if page.video_entry_id:
        assets.append("video")
    return ",".join(assets) or "sin archivos"


def _cache_path(config: SessionConfig, filename: str) -> Path:
    path = config.root / "cache" / str(config.id) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _unusable_asset(blob: bytes, source: str) -> bool:
    if is_waf_challenge(blob):
        return True
    if source.startswith("pdf"):
        stripped = blob.lstrip()
        if stripped.startswith(b"%PDF"):
            return False
        head = stripped[:32].lower()
        return head.startswith(b"<!doctype") or head.startswith(b"<html")
    return False


def _download_error(source: str, filename: str, exc: HttpError) -> str:
    if exc.status == 202 or "WAF" in str(exc):
        return (
            f"{source} ({filename}): WAF HTTP {exc.status or 202} en gadebate; "
            "sigo la cascada"
        )
    return f"{source} ({filename}): HTTP {exc.status or '?'} {exc}"


def extract_from_page(
    config: SessionConfig,
    page: SpeakerPage,
    *,
    dest: Path | None = None,
) -> ExtractedSpeech:
    speech, _, _ = extract_from_page_timed(config, page, dest=dest)
    return speech


def extract_from_page_timed(
    config: SessionConfig,
    page: SpeakerPage,
    *,
    dest: Path | None = None,
) -> tuple[ExtractedSpeech, str, float]:
    if page.error:
        raise SourceUnavailable(page.error)
    errors: list[str] = []
    started = time.perf_counter()
    via = ""
    for source in config.sources:
        try:
            speech, via = _extract_source(config, page, source)
            directory = out_dir(config, page.speech_date, dest=dest)
            txt_path = write_speech(speech, directory)
            append_manifest(directory, speech, txt_path)
            return speech, via, time.perf_counter() - started
        except (SourceUnavailable, HttpError) as exc:
            errors.append(str(exc))
            print(
                f"  {source} no usable: {exc}",
                file=sys.stderr,
                flush=True,
            )
            continue
    raise SourceUnavailable("; ".join(errors) or "ningún origen de la cascada está disponible")


def _extract_source(
    config: SessionConfig,
    page: SpeakerPage,
    source: str,
) -> tuple[ExtractedSpeech, str]:
    mapping = {
        "pdf_en": page.pdf_en,
        "audio_en": page.audio_en,
        "pdf_other": page.pdf_other,
    }
    if source == "video":
        return _extract_video(config, page)
    ref = mapping.get(source)
    if ref is None:
        raise SourceUnavailable(f"{source}: no está en la ficha")
    cache = _cache_path(config, ref.filename)
    cached = (
        cache.read_bytes()
        if cache.is_file() and cache.stat().st_size > 0
        else b""
    )
    if cached and not _unusable_asset(cached, source):
        blob = cached
    else:
        if cached:
            cache.unlink(missing_ok=True)
        timeout = 180.0 if source == "audio_en" else 40.0
        try:
            _, _, blob = fetch(ref.url, user_agent=config.user_agent, timeout=timeout)
        except HttpError as exc:
            raise SourceUnavailable(_download_error(source, ref.filename, exc)) from exc
        if _unusable_asset(blob, source):
            raise SourceUnavailable(
                f"{source} ({ref.filename}): HTML/WAF en vez del archivo; sigo la cascada"
            )
        cache.write_bytes(blob)
    if source == "audio_en":
        text = transcribe_audio_file(
            cache,
            language=language_for(source, ref),
            model_name=config.whisper_model,
            device=config.whisper_device,
            compute_type=config.whisper_compute_type,
            beam_size=config.whisper_beam_size,
            vad=config.whisper_vad,
        )
        if len(text) < config.min_audio_chars:
            raise SourceUnavailable(
                f"{source} tiene solo {len(text)} caracteres "
                f"(mínimo {config.min_audio_chars})"
            )
        return _speech(
            config,
            page,
            source=source,
            source_url=ref.url,
            language=language_for(source, ref),
            text=text,
        ), "whisper"
    text = extract_pdf_text(blob)
    used_ocr = False
    if len(text) < config.min_pdf_chars:
        iso = language_for(source, ref)
        tess_lang = "eng" if source == "pdf_en" else tesseract_lang_for(iso)
        ocr_cache = cache.with_name(f"{cache.stem}.ocr.txt")
        text = ocr_pdf_text(
            blob,
            lang=tess_lang,
            cache_path=ocr_cache,
            label=ref.filename,
        )
        used_ocr = True
    if len(text) < config.min_pdf_chars:
        if not text.strip():
            raise SourceUnavailable(
                f"{source} ({ref.filename}): sin texto extraíble "
                "(pypdf/OCR vacíos); sigo la cascada"
            )
        raise SourceUnavailable(
            f"{source} ({ref.filename}) tiene solo {len(text)} caracteres "
            f"(mínimo {config.min_pdf_chars}); probable PDF ilegible"
        )
    if source == "pdf_other":
        from pipeline.translate import translate_to_english

        text = translate_to_english(text, source_lang=language_for(source, ref))
        return _speech(
            config,
            page,
            source=source,
            source_url=ref.url,
            language="en",
            text=text,
            transformation="translate",
        ), "translate"
    return _speech(
        config,
        page,
        source=source,
        source_url=ref.url,
        language=language_for(source, ref),
        text=text,
        transformation="ocr" if used_ocr else None,
    ), ("ocr" if used_ocr else "pypdf")


def _extract_video(config: SessionConfig, page: SpeakerPage) -> tuple[ExtractedSpeech, str]:
    from pipeline.extract_video import kaltura_play_url, transcribe_kaltura

    if not page.video_entry_id:
        raise SourceUnavailable("video: no hay entry de Kaltura")
    wav = _cache_path(config, f"{page.video_entry_id}.wav")
    original = original_language_for(page)
    whisper_lang: str | None = original if original not in {"", "und"} else None
    text = transcribe_kaltura(
        page.video_entry_id,
        wav,
        partner_id=page.video_partner_id,
        user_agent=config.user_agent,
        language=whisper_lang,
        model_name=config.whisper_model,
        device=config.whisper_device,
        compute_type=config.whisper_compute_type,
        beam_size=config.whisper_beam_size,
        vad=config.whisper_vad,
    )
    if len(text) < config.min_audio_chars:
        raise SourceUnavailable(
            f"video tiene solo {len(text)} caracteres "
            f"(mínimo {config.min_audio_chars})"
        )
    xf = "whisper"
    lang = original if original != "und" else "und"
    if original not in {"", "und", "en"}:
        from pipeline.translate import translate_to_english

        text = translate_to_english(text, source_lang=original)
        xf = "translate"
        lang = "en"
    return _speech(
        config,
        page,
        source="video",
        source_url=kaltura_play_url(page.video_entry_id, page.video_partner_id),
        language=lang,
        text=text,
        transformation=xf,
    ), ("translate" if xf == "translate" else "whisper")


def fetch_speeches(
    config: SessionConfig,
    *,
    day: str | None = None,
    slug: str | None = None,
    limit: int | None = None,
    dest: Path | None = None,
    metadata_only: bool = False,
    skip_existing: bool = False,
    require_roster: bool = False,
) -> list[FetchItem]:
    from pipeline.roster import load_roster, page_from_entry, roster_path

    roster = load_roster(config, day) if day else None
    if require_roster and day and roster is None:
        raise FileNotFoundError(
            f"no hay roster {roster_path(config, day)}; "
            "corre `python -m pipeline roster --session ... --day ...` en la laptop y subilo a git"
        )
    pages_by_slug: dict[str, SpeakerPage] = {}
    if roster:
        print(
            f"roster {roster_path(config, day)} "
            f"({len(roster.get('speakers') or [])} fichas); no scrape de gadebate",
            file=sys.stderr,
            flush=True,
        )
        for entry in roster.get("speakers") or []:
            page = page_from_entry(entry)
            if page.slug:
                pages_by_slug[page.slug] = page
        if slug:
            slugs = [slug]
        else:
            slugs = list(pages_by_slug)
            if limit is not None:
                slugs = slugs[:limit]
    else:
        slugs = select_slugs(config, day=day, slug=slug, limit=limit)
    items: list[FetchItem] = []
    for item in slugs:
        existing = find_existing_speech(config, item, dest=dest)
        if skip_existing and existing and is_english_transcript(existing):
            items.append(
                FetchItem(
                    page=SpeakerPage(
                        slug=item,
                        url=config.speaker_url(item),
                        country="",
                        name="",
                        rank="",
                        speaker_title="",
                        speech_date="",
                    ),
                    skip="already extracted",
                )
            )
            continue
        if item in pages_by_slug:
            page = pages_by_slug[item]
        elif require_roster:
            missing = f"no está en el roster de {day}"
            items.append(
                FetchItem(
                    page=SpeakerPage(
                        slug=item,
                        url=config.speaker_url(item),
                        country="",
                        name="",
                        rank="",
                        speaker_title="",
                        speech_date=day or "",
                        error=missing,
                    ),
                    skip=missing,
                )
            )
            continue
        else:
            page = scrape_speaker(config, item)
        if day and page.speech_date and page.speech_date != day:
            continue
        print(
            f"FICHA {page.slug} date={page.speech_date or '?'} "
            f"http={page.http_status or '?'} "
            f"{page.country} | {page.name} | {_asset_labels(page)}",
            file=sys.stderr,
            flush=True,
        )
        if metadata_only or page.error:
            items.append(FetchItem(page=page, skip=page.error))
            continue
        try:
            speech, via, elapsed = extract_from_page_timed(config, page, dest=dest)
            items.append(
                FetchItem(page=page, speech=speech, via=via, elapsed_s=elapsed)
            )
        except SourceUnavailable as exc:
            items.append(FetchItem(page=page, skip=str(exc)))
    return items
