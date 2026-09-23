"""Cliente HTTP de Anthropic Messages (analyze + coding)."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

from pipeline.env import load_dotenv

DEFAULT_MODEL = "claude-haiku-4-5"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.I)


class ClaudeError(RuntimeError):
    pass


def _decode_json_objects(text: str) -> list[dict]:
    decoder = json.JSONDecoder()
    objects: list[dict] = []
    idx = 0
    while idx < len(text):
        start = text.find("{", idx)
        if start < 0:
            break
        try:
            data, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            idx = start + 1
            continue
        if isinstance(data, dict):
            objects.append(data)
        idx = max(end, start + 1)
    return objects


def _merge_json_objects(objects: list[dict]) -> dict:
    if len(objects) == 1:
        return objects[0]
    merged: dict = {}
    for obj in objects:
        for key, value in obj.items():
            if (
                key in merged
                and isinstance(merged[key], list)
                and isinstance(value, list)
            ):
                merged[key].extend(value)
            elif key not in merged:
                merged[key] = value
    return merged


def parse_json_object(raw: str) -> dict:
    text = (raw or "").strip()
    if not text:
        raise ClaudeError("Claude devolvió texto vacío")
    fenced = [match.group(1).strip() for match in _JSON_FENCE.finditer(text)]
    objects: list[dict] = []
    for chunk in fenced or [text]:
        objects.extend(_decode_json_objects(chunk))
    if not objects:
        raise ClaudeError(f"no hay JSON en la respuesta: {text[:200]!r}")
    merged = _merge_json_objects(objects)
    if not isinstance(merged, dict):
        raise ClaudeError("Claude no devolvió un objeto JSON")
    return merged


def anthropic_settings() -> tuple[str, str]:
    load_dotenv()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    model = os.environ.get("ANTHROPIC_MODEL", "").strip() or DEFAULT_MODEL
    if not api_key:
        raise ClaudeError("falta ANTHROPIC_API_KEY en .env")
    return api_key, model


def anthropic_workspace_id() -> str:
    load_dotenv()
    return os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()


def _request_headers(api_key: str) -> dict[str, str]:
    headers = {
        "content-type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    workspace = anthropic_workspace_id()
    if workspace:
        headers["anthropic-workspace-id"] = workspace
    return headers


def _http_error_message(code: int, detail: str) -> str:
    message = f"Anthropic HTTP {code}: {detail}"
    lower = detail.lower()
    if "workspace" in lower and not anthropic_workspace_id():
        message += (
            " Poné ANTHROPIC_WORKSPACE_ID=wrkspc_... en .env "
            "(Console → Settings → Workspaces) o usá una API key creada "
            "dentro de un workspace."
        )
    return message


def post_messages(payload: dict, api_key: str, timeout: float = 120) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=body,
        method="POST",
        headers=_request_headers(api_key),
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise ClaudeError(_http_error_message(exc.code, detail)) from exc
    except urllib.error.URLError as exc:
        raise ClaudeError(f"Anthropic red: {exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ClaudeError("Anthropic no devolvió JSON") from exc
    if not isinstance(data, dict):
        raise ClaudeError("Anthropic: cuerpo inesperado")
    return data


def call_claude(
    user_message: str,
    *,
    api_key: str,
    model: str,
    system: str,
    max_tokens: int = 2048,
    timeout: float = 120,
    cache_system: bool = False,
    disable_thinking: bool = False,
) -> dict:
    payload: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": user_message}],
    }
    # Sonnet 5+ activa adaptive thinking por default y come el presupuesto
    # de max_tokens; para JSON de coding conviene apagarlo.
    if disable_thinking or "sonnet-5" in (model or "").lower():
        payload["thinking"] = {"type": "disabled"}
    if cache_system:
        payload["system"] = [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            }
        ]
    else:
        payload["system"] = system
    data = post_messages(payload, api_key, timeout=timeout)
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    if cache_system or usage:
        print(
            f"claude usage in={usage.get('input_tokens', 0)} "
            f"out={usage.get('output_tokens', 0)} "
            f"cache_write={usage.get('cache_creation_input_tokens', 0)} "
            f"cache_read={usage.get('cache_read_input_tokens', 0)} "
            f"stop={data.get('stop_reason') or '?'}",
            file=sys.stderr,
        )
    out_tokens = int(usage.get("output_tokens") or 0)
    if data.get("stop_reason") == "max_tokens" or (
        max_tokens > 0 and out_tokens >= max_tokens
    ):
        raise ClaudeError(
            f"respuesta truncada (out={out_tokens} max_tokens={max_tokens}); "
            "bajá el chunk o subí CODING_MAX_TOKENS"
        )
    chunks = data.get("content") or []
    texts: list[str] = []
    for chunk in chunks:
        if isinstance(chunk, dict) and chunk.get("type") == "text":
            texts.append(str(chunk.get("text") or ""))
    return parse_json_object("\n".join(texts))
