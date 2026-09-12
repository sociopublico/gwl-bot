from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from pipeline.cascade import SourceUnavailable
from pipeline.config import SessionConfig, load_date_index, load_slugs
from pipeline.extract_audio import transcribe_audio_file
from pipeline.extract_ocr import ocr_pdf_text, tesseract_lang_for
from pipeline.extract_pdf import extract_pdf_text
from pipeline.gadebate import scrape_speaker
from pipeline.http import fetch
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


def extract_from_page(
    config: SessionConfig,
    page: SpeakerPage,
    *,
    dest: Path | None = None,
) -> ExtractedSpeech:
    if page.error:
        raise SourceUnavailable(page.error)
    errors: list[str] = []
    for source in config.sources:
        try:
            speech = _extract_source(config, page, source)
            directory = out_dir(config, page.speech_date, dest=dest)
            txt_path = write_speech(speech, directory)
            append_manifest(directory, speech, txt_path)
            return speech
        except SourceUnavailable as exc:
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
) -> ExtractedSpeech:
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
    if cache.is_file() and cache.stat().st_size > 0:
        blob = cache.read_bytes()
    else:
        timeout = 180.0 if source == "audio_en" else 40.0
        _, _, blob = fetch(ref.url, user_agent=config.user_agent, timeout=timeout)
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
    else:
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
            )
        return _speech(
            config,
            page,
            source=source,
            source_url=ref.url,
            language=language_for(source, ref),
            text=text,
            transformation="ocr" if used_ocr else None,
        )
    return _speech(
        config,
        page,
        source=source,
        source_url=ref.url,
        language=language_for(source, ref),
        text=text,
    )


def _extract_video(config: SessionConfig, page: SpeakerPage) -> ExtractedSpeech:
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
    )


def fetch_speeches(
    config: SessionConfig,
    *,
    day: str | None = None,
    slug: str | None = None,
    limit: int | None = None,
    dest: Path | None = None,
    metadata_only: bool = False,
    skip_existing: bool = False,
) -> list[FetchItem]:
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
            speech = extract_from_page(config, page, dest=dest)
            items.append(FetchItem(page=page, speech=speech))
        except SourceUnavailable as exc:
            items.append(FetchItem(page=page, skip=str(exc)))
    return items
