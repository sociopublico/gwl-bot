from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FileRef:
    label: str
    url: str
    filename: str
    lang: str = ""


@dataclass
class SpeakerPage:
    slug: str
    url: str
    country: str
    name: str
    rank: str
    speaker_title: str
    speech_date: str
    pdfs: list[FileRef] = field(default_factory=list)
    audios: list[FileRef] = field(default_factory=list)
    pdf_en: FileRef | None = None
    pdf_other: FileRef | None = None
    audio_en: FileRef | None = None
    audio_floor: FileRef | None = None
    transcript_ai: FileRef | None = None
    video_entry_id: str | None = None
    video_partner_id: str | None = None
    error: str | None = None
    http_status: int | None = None


@dataclass
class ExtractedSpeech:
    session_id: int
    slug: str
    country: str
    name: str
    rank: str
    speech_date: str
    source: str
    source_url: str
    language: str
    text: str
    skipped: str | None = None
    speaker_title: str = ""
    original_language: str = ""
    transformation: str = "none"
    id_speech: str = ""
    via: str = ""
    elapsed_s: float = 0.0
