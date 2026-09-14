"""Paso 4: Claude API → filas en la pestaña Analysis.

El prompt vive en pipeline/data/analyze-prompt.md (lo pegan cuando esté).
Las columnas se leen del encabezado de la pestaña; si la hoja está vacía se
usan ANALYSIS_COLUMNS (identidad + summary/notes de placeholder).
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from pipeline.config import PIPELINE_ROOT, SessionConfig
from pipeline.env import load_dotenv
from pipeline.models import ExtractedSpeech
from pipeline.sheets import (
    ANALYSIS_COLUMNS,
    ANALYSIS_IDENTITY,
    append_analysis_rows,
    can_write_sheets,
    sheets_settings,
    sheets_unavailable_reason,
)
from pipeline.store import list_speech_txts, parse_speech_txt

DEFAULT_PROMPT_PATH = PIPELINE_ROOT / "data" / "analyze-prompt.md"
DEFAULT_MODEL = "claude-haiku-4-5"
STUB_MARKERS = ("TODO: pegar el prompt", "PEGAR_PROMPT")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.I)


class AnalyzeError(RuntimeError):
    pass


def prompt_is_stub(text: str) -> bool:
    lower = text.lower()
    return any(marker.lower() in lower for marker in STUB_MARKERS)


def load_prompt(path: Path | None = None) -> str:
    prompt_path = path or DEFAULT_PROMPT_PATH
    if not prompt_path.is_file():
        raise AnalyzeError(f"no encuentro el prompt en {prompt_path}")
    text = prompt_path.read_text(encoding="utf-8").strip()
    if not text:
        raise AnalyzeError(f"prompt vacío: {prompt_path}")
    return text


def parse_json_object(raw: str) -> dict:
    text = (raw or "").strip()
    if not text:
        raise AnalyzeError("Claude devolvió texto vacío")
    fenced = _JSON_FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise AnalyzeError(f"no hay JSON en la respuesta: {text[:200]!r}")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AnalyzeError(f"JSON inválido: {exc}") from exc
    if not isinstance(data, dict):
        raise AnalyzeError("Claude no devolvió un objeto JSON")
    return data


def _ficha_url(speech: ExtractedSpeech) -> str:
    if speech.session_id and speech.slug:
        return f"https://gadebate.un.org/en/{speech.session_id}/{speech.slug}"
    return ""


def identity_fields(speech: ExtractedSpeech) -> dict[str, str]:
    return {
        "slug": speech.slug,
        "date_time": speech.speech_date,
        "country": speech.country,
        "speaker_name": speech.name,
        "ficha_url": _ficha_url(speech),
    }


def analysis_key(speech: ExtractedSpeech) -> str:
    return f"{speech.slug}|{speech.speech_date}"


def build_user_message(
    prompt: str,
    speech: ExtractedSpeech,
    *,
    columns: list[str],
) -> str:
    identity = identity_fields(speech)
    return (
        f"{prompt.strip()}\n\n"
        "Respondé únicamente un objeto JSON (sin markdown) con exactamente "
        f"estas claves: {json.dumps(columns, ensure_ascii=False)}.\n"
        "Los campos de identidad ya están definidos; copialos tal cual:\n"
        f"{json.dumps(identity, ensure_ascii=False)}\n\n"
        f"DISCURSO ({speech.slug} {speech.speech_date}):\n{speech.text.strip()}\n"
    )


def _anthropic_settings() -> tuple[str, str]:
    load_dotenv()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    model = os.environ.get("ANTHROPIC_MODEL", "").strip() or DEFAULT_MODEL
    if not api_key:
        raise AnalyzeError("falta ANTHROPIC_API_KEY en .env")
    return api_key, model


def allow_stub() -> bool:
    load_dotenv()
    return os.environ.get("ANALYZE_ALLOW_STUB", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _post_messages(payload: dict, api_key: str, timeout: float = 120) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=body,
        method="POST",
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise AnalyzeError(f"Anthropic HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise AnalyzeError(f"Anthropic red: {exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise AnalyzeError("Anthropic no devolvió JSON") from exc
    if not isinstance(data, dict):
        raise AnalyzeError("Anthropic: cuerpo inesperado")
    return data


def call_claude(user_message: str, *, api_key: str, model: str) -> dict:
    payload = {
        "model": model,
        "max_tokens": 2048,
        "system": (
            "Sos un analista de discursos de la Asamblea General. "
            "Respondé solo un objeto JSON válido, sin texto alrededor."
        ),
        "messages": [{"role": "user", "content": user_message}],
    }
    data = _post_messages(payload, api_key)
    chunks = data.get("content") or []
    texts: list[str] = []
    for chunk in chunks:
        if isinstance(chunk, dict) and chunk.get("type") == "text":
            texts.append(str(chunk.get("text") or ""))
    return parse_json_object("\n".join(texts))


def merge_analysis_row(
    speech: ExtractedSpeech,
    model_row: dict,
    *,
    columns: list[str],
) -> dict[str, str]:
    identity = identity_fields(speech)
    row: dict[str, str] = {}
    for col in columns:
        if col in ANALYSIS_IDENTITY:
            row[col] = identity.get(col, "")
            continue
        value = model_row.get(col, "")
        if value is None:
            row[col] = ""
        elif isinstance(value, (dict, list)):
            row[col] = json.dumps(value, ensure_ascii=False)
        else:
            row[col] = str(value).strip()
    return row


def _speeches_for_day(
    config: SessionConfig,
    *,
    day: str,
    slug: str | None,
    dest: Path | None,
) -> list[ExtractedSpeech]:
    paths = list_speech_txts(config, speech_date=day, dest=dest)
    speeches: list[ExtractedSpeech] = []
    for path in paths:
        if path.name == "manifest.json":
            continue
        try:
            speech = parse_speech_txt(path)
        except ValueError as exc:
            print(f"SKIP {path.name}: {exc}", file=sys.stderr)
            continue
        if slug and speech.slug != slug:
            continue
        speeches.append(speech)
    return speeches


def analyze_day(
    config: SessionConfig,
    *,
    day: str,
    slug: str | None = None,
    dest: Path | None = None,
    prompt_path: Path | None = None,
    dry_run: bool = False,
) -> int:
    try:
        prompt = load_prompt(prompt_path)
    except AnalyzeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    stub = prompt_is_stub(prompt)
    if stub and not dry_run and not allow_stub():
        print(
            "error: pipeline/data/analyze-prompt.md sigue siendo un stub. "
            "Pegá el prompt de coding (y columnas de ejemplo en la pestaña "
            "Analysis) antes de correr analyze. "
            "Para una prueba: ANALYZE_ALLOW_STUB=1 o --dry-run.",
            file=sys.stderr,
        )
        return 1

    speeches = _speeches_for_day(config, day=day, slug=slug, dest=dest)
    if not speeches:
        print(
            f"error: no hay .txt en pipeline/out/{config.id}/{day}",
            file=sys.stderr,
        )
        return 1

    settings = sheets_settings()
    columns = list(ANALYSIS_COLUMNS)
    if not dry_run:
        if not can_write_sheets(settings):
            print(f"error: {sheets_unavailable_reason(settings)}", file=sys.stderr)
            return 1
        from pipeline.sheets import analysis_headers

        columns = analysis_headers(settings)

    print(
        f"analyze session={config.id} day={day} speeches={len(speeches)} "
        f"columns={len(columns)} stub={int(stub)}",
        file=sys.stderr,
    )
    if dry_run:
        for speech in speeches:
            print(f"DRY {analysis_key(speech)} chars={len(speech.text)}", file=sys.stderr)
        print(
            json.dumps(
                {
                    "day": day,
                    "speeches": len(speeches),
                    "columns": columns,
                    "stub": stub,
                }
            ),
            file=sys.stderr,
        )
        return 0

    try:
        api_key, model = _anthropic_settings()
    except AnalyzeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    rows: list[dict[str, str]] = []
    errors = 0
    for speech in speeches:
        message = build_user_message(prompt, speech, columns=columns)
        try:
            parsed = call_claude(message, api_key=api_key, model=model)
            row = merge_analysis_row(speech, parsed, columns=columns)
            rows.append(row)
            print(f"OK {analysis_key(speech)} model={model}", file=sys.stderr)
        except AnalyzeError as exc:
            errors += 1
            print(f"FAIL {analysis_key(speech)} {exc}", file=sys.stderr)

    written = append_analysis_rows(rows, headers=columns, settings=settings)
    print(
        json.dumps(
            {
                "analyzed": len(rows),
                "written": len(written),
                "skipped": len(rows) - len(written),
                "errors": errors,
                "model": model,
            }
        ),
        file=sys.stderr,
    )
    return 0 if errors == 0 else 2
