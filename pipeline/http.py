from __future__ import annotations

import ssl
import time
import urllib.request
from urllib.error import HTTPError
from urllib.request import Request

_CTX = ssl.create_default_context()

# Headers de navegador; sin esto CloudFront a veces contesta raro.
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
    # urllib respeta HTTP_PROXY del .env (Traefik, etc.) y gadebate termina en 202 vacío.
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=_CTX),
        urllib.request.HTTPHandler(),
    )


def _unusable_get(status: int, body: bytes) -> bool:
    if status == 202:
        return True
    if status == 200 and not body:
        return True
    return False


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
    for attempt in range(retries):
        try:
            with opener.open(req, timeout=timeout) as resp:
                body = resp.read() if method != "HEAD" else b""
                status = int(resp.status)
                if method != "HEAD" and _unusable_get(status, body):
                    last_err = HttpError(
                        url,
                        status,
                        f"respuesta vacía o {status} ({len(body)} bytes)",
                    )
                    time.sleep(1.1 * (attempt + 1))
                    continue
                return status, dict(resp.headers), body
        except HTTPError as exc:
            last_err = exc
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
    status = getattr(last_err, "status", None) or getattr(last_err, "code", None)
    raise HttpError(url, status, str(last_err)) from last_err
