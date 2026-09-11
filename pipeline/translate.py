from __future__ import annotations

import hashlib
import re
import sys
import time
import unicodedata

from pipeline.cascade import SourceUnavailable
from pipeline.config import PIPELINE_ROOT

# Google Translate nooficial (deep-translator). Límite ~5000 chars por request.
_CHUNK = 2500
_GOOGLE_LANG = {
    "zh": "zh-CN",
    "iw": "he",
    "nb": "no",
    "fil": "tl",
}


def _chunks(text: str) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    if not paras:
        return [text.strip()] if text.strip() else []
    out: list[str] = []
    buf = ""
    for para in paras:
        if buf and len(buf) + 2 + len(para) > _CHUNK:
            out.append(buf)
            buf = para
        else:
            buf = f"{buf}\n\n{para}".strip() if buf else para
        while len(buf) > _CHUNK:
            out.append(buf[:_CHUNK])
            buf = buf[_CHUNK:].lstrip()
    if buf:
        out.append(buf)
    return out


def _short_err(exc: BaseException) -> str:
    msg = str(exc)
    if "-->" in msg:
        msg = msg.split("-->", 1)[-1].strip()
    return re.sub(r"\s+", " ", msg)[:180]


def _split_chunk(chunk: str) -> tuple[str, str] | None:
    if len(chunk) < 800:
        return None
    mid = len(chunk) // 2
    for sep in ("\n\n", ". ", " "):
        cut = chunk.rfind(sep, 400, mid + 200)
        if cut >= 400:
            left, right = chunk[: cut + len(sep)].strip(), chunk[cut + len(sep) :].strip()
            if left and right:
                return left, right
    return chunk[:mid].strip(), chunk[mid:].strip()


def _looks_translated(src: str, dst: str) -> bool:
    out = (dst or "").strip()
    if len(out) < 8:
        return False
    low = out.lower()
    if "error 500" in low or "no translation was found" in low:
        return False
    if len(src) >= 80 and len(out) < max(40, int(len(src) * 0.25)):
        return False
    return True


def _translate_via_fallback(chunk: str, source_lang: str) -> str | None:
    try:
        import translators as ts
    except ImportError:
        return None
    src = source_lang if source_lang not in {"", "auto", "und"} else "auto"
    for engine in ("bing", "google", "yandex"):
        try:
            translated = ts.translate_text(
                chunk,
                translator=engine,
                from_language=src,
                to_language="en",
            )
        except Exception:
            continue
        if _looks_translated(chunk, translated or ""):
            print(f"  translate fallback={engine}", file=sys.stderr, flush=True)
            return (translated or "").strip()
    return None


def _translate_chunk(translator, chunk: str, *, source_lang: str, tries: int = 3) -> str:
    last: BaseException | None = None
    chunk = unicodedata.normalize("NFKC", chunk)
    for attempt in range(1, tries + 1):
        try:
            translated = translator.translate(chunk)
            if _looks_translated(chunk, translated or ""):
                return (translated or "").strip()
            last = RuntimeError("chunk vacío o truncado")
        except Exception as exc:  # noqa: BLE001
            last = exc
        fallback = _translate_via_fallback(chunk, source_lang)
        if fallback:
            return fallback
        time.sleep(1.2 * attempt)
    parts = _split_chunk(chunk)
    if parts:
        left, right = parts
        return (
            _translate_chunk(translator, left, source_lang=source_lang, tries=tries)
            + "\n\n"
            + _translate_chunk(translator, right, source_lang=source_lang, tries=tries)
        )
    raise SourceUnavailable(
        f"translate: falló Google Translate ({_short_err(last or RuntimeError('unknown'))})"
    )


def translate_to_english(text: str, *, source_lang: str = "auto") -> str:
    """Traduce al inglés. Requiere deep-translator (Google, sin API key)."""
    body = unicodedata.normalize("NFKC", (text or "").strip())
    if not body:
        raise SourceUnavailable("translate: texto vacío")
    src = (source_lang or "auto").strip().lower()
    if src in {"", "und", "unknown", "en", "english"}:
        if src in {"en", "english"}:
            return body
        src = "auto"
    src = _GOOGLE_LANG.get(src, src)
    try:
        from deep_translator import GoogleTranslator
    except ImportError as exc:
        raise SourceUnavailable(
            "pdf_other: falta deep-translator para traducir a inglés. "
            "Instalalo con: pipeline/.venv/bin/pip install -r pipeline/requirements.txt"
        ) from exc
    cache_dir = PIPELINE_ROOT / "cache" / "translate"
    digest = hashlib.sha256(f"{src}\n{body}".encode("utf-8")).hexdigest()[:24]
    cache_path = cache_dir / f"{digest}.txt"
    if cache_path.is_file() and cache_path.stat().st_size > 0:
        return cache_path.read_text(encoding="utf-8")
    translator = GoogleTranslator(source=src, target="en")
    parts = _chunks(body)
    print(f"translate {src}->en chunks={len(parts)} chars={len(body)}", file=sys.stderr, flush=True)
    pieces: list[str] = []
    for i, chunk in enumerate(parts, start=1):
        pieces.append(_translate_chunk(translator, chunk, source_lang=src))
        if i % 3 == 0 or i == len(parts):
            print(f"  translate {i}/{len(parts)}", file=sys.stderr, flush=True)
    result = "\n\n".join(pieces).strip()
    if len(result) < 50:
        raise SourceUnavailable(f"translate: resultado demasiado corto ({len(result)} chars)")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(result, encoding="utf-8")
    return result
