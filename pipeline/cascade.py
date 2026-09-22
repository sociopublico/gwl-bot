from __future__ import annotations

from pipeline.models import FileRef, SpeakerPage


class SourceUnavailable(RuntimeError):
    pass


def choose_source(page: SpeakerPage, order: tuple[str, ...]) -> tuple[str, FileRef]:
    """Devuelve (source_id, archivo) según la cascada configurada."""
    mapping: dict[str, FileRef | None] = {
        "pdf_en": page.pdf_en,
        "transcript_ai": page.transcript_ai,
        "audio_en": page.audio_en,
        "pdf_other": page.pdf_other,
        "video": None,
    }
    for source in order:
        if source == "video":
            if page.video_entry_id:
                from pipeline.extract_video import kaltura_play_url

                url = kaltura_play_url(page.video_entry_id, page.video_partner_id)
                return source, FileRef(
                    "kaltura",
                    url,
                    f"{page.video_entry_id}.mp4",
                )
            continue
        ref = mapping.get(source)
        if ref is not None:
            return source, ref
    raise SourceUnavailable("ningún origen de la cascada está disponible en la ficha")
