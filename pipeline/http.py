from __future__ import annotations

import shutil
import ssl
import subprocess
import time
import urllib.request
from urllib.error import HTTPError
from urllib.request import Request

_CTX = ssl.create_default_context()

_BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class HttpError(RuntimeError):
    def __init__(self, url: str, status: int | None, message: str) -> None:
        super().__init__(f"{url} -> {status}: {message}")
        self.url = url
        self.status = status


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=_CTX),
        urllib.request.HTTPHandler(),
    )


def _looks_like_speaker_html(body: bytes) -> bool:
    text = body[:80_000].decode("utf-8", "replace").lower()
    return (
        "full statement" in text
        or "field--name-field-speaker" in text
        or "gastatements" in text
        or "/en/80/" in text
        or "/en/81/" in text
    )


def is_waf_challenge(body: bytes) -> bool:
    """CloudFront/AWS WAF interstitial (el VPS lo recibe en gadebate.un.org)."""
    text = body[:20_000].decode("utf-8", "replace").lower()
    return (
        "awswafcookiedomainlist" in text
        or "gokuprops" in text
        or "captcha.awswaf.com" in text
        or "x-amzn-waf-action" in text
    )


def _preview(body: bytes, limit: int = 280) -> str:
    text = body.decode("utf-8", "replace").replace("\n", " ").strip()
    text = " ".join(text.split())
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def _unusable_get(status: int, body: bytes) -> bool:
    if is_waf_challenge(body):
        return True
    if status == 200 and not body:
        return True
    if status == 202 and not _looks_like_speaker_html(body):
        return True
    return False


def _fetch_curl(
    url: str,
    *,
    user_agent: str,
    timeout: float,
    extra_headers: dict[str, str] | None,
) -> tuple[int, dict[str, str], bytes] | None:
    binary = shutil.which("curl")
    if not binary:
        return None
    cmd = [
        binary,
        "-sS",
        "-L",
        "--http1.1",
        "--max-time",
        str(max(5, int(timeout))),
        "-A",
        user_agent,
        "-D",
        "-",
        "-o",
        "-",
        "-w",
        "\n__GWL_HTTP_CODE__:%{http_code}",
    ]
    for key, value in {**_BROWSER_HEADERS, **(extra_headers or {})}.items():
        cmd.extend(["-H", f"{key}: {value}"])
    cmd.append(url)
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            timeout=timeout + 8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    raw = proc.stdout or b""
    marker = b"\n__GWL_HTTP_CODE__:"
    if marker not in raw:
        return None
    payload, _, code_raw = raw.rpartition(marker)
    try:
        status = int(code_raw.strip() or b"0")
    except ValueError:
        return None
    header_blob, sep, body = payload.partition(b"\r\n\r\n")
    if not sep:
        header_blob, sep, body = payload.partition(b"\n\n")
    if not sep:
        body = payload
    if _unusable_get(status, body):
        return None
    return status, {}, body


def fetch(
    url: str,
    *,
    user_agent: str,
    method: str = "GET",
    timeout: float = 40,
    retries: int = 4,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    headers = {"User-Agent": user_agent, **_BROWSER_HEADERS, **(extra_headers or {})}
    req = Request(url, headers=headers, method=method)
    opener = _opener()
    last_err: Exception | None = None
    last_body = b""
    last_status: int | None = None
    for attempt in range(retries):
        try:
            with opener.open(req, timeout=timeout) as resp:
                body = resp.read() if method != "HEAD" else b""
                status = int(resp.status)
                last_status = status
                last_body = body
                location = (resp.headers.get("Location") or "").strip()
                if (
                    method != "HEAD"
                    and status == 202
                    and location
                    and location != url
                ):
                    return fetch(
                        location,
                        user_agent=user_agent,
                        method=method,
                        timeout=timeout,
                        retries=max(1, retries - 1),
                        extra_headers=extra_headers,
                    )
                if method != "HEAD" and is_waf_challenge(body):
                    raise HttpError(
                        url,
                        status,
                        f"WAF challenge HTTP {status} ({len(body)} bytes)",
                    )
                if method != "HEAD" and _unusable_get(status, body):
                    last_err = HttpError(
                        url,
                        status,
                        f"respuesta {status} ({len(body)} bytes) {_preview(body)}",
                    )
                    time.sleep(1.1 * (attempt + 1))
                    continue
                return status, dict(resp.headers), body
        except HttpError:
            raise
        except HTTPError as exc:
            last_err = exc
            last_status = exc.code
            if exc.code in (405, 501) and method == "HEAD":
                return fetch(
                    url,
                    user_agent=user_agent,
                    method="GET",
                    timeout=timeout,
                    retries=retries,
                    extra_headers=extra_headers,
                )
            if exc.code in (404, 403):
                raise HttpError(url, exc.code, exc.reason) from exc
            time.sleep(1.1 * (attempt + 1))
        except Exception as exc:  # noqa: BLE001 — reintento de red
            last_err = exc
            time.sleep(1.1 * (attempt + 1))
    if method != "HEAD":
        via_curl = _fetch_curl(
            url,
            user_agent=user_agent,
            timeout=timeout,
            extra_headers=extra_headers,
        )
        if via_curl is not None:
            return via_curl
    status = last_status or getattr(last_err, "status", None) or getattr(
        last_err, "code", None
    )
    detail = _preview(last_body) if last_body else str(last_err)
    raise HttpError(url, status, detail) from last_err
