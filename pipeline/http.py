from __future__ import annotations

import ssl
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

_CTX = ssl.create_default_context()


class HttpError(RuntimeError):
    def __init__(self, url: str, status: int | None, message: str) -> None:
        super().__init__(f"{url} -> {status}: {message}")
        self.url = url
        self.status = status


def fetch(
    url: str,
    *,
    user_agent: str,
    method: str = "GET",
    timeout: float = 40,
    retries: int = 4,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    headers = {"User-Agent": user_agent, **(extra_headers or {})}
    req = Request(url, headers=headers, method=method)
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            with urlopen(req, timeout=timeout, context=_CTX) as resp:
                body = resp.read() if method != "HEAD" else b""
                return int(resp.status), dict(resp.headers), body
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
    status = getattr(last_err, "code", None)
    raise HttpError(url, status, str(last_err)) from last_err
