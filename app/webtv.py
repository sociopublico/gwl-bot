from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def webtv_url_at(asset_url: str, seconds: float) -> str:
    """Link al segundo en UN Web TV.

    El player Kaltura PlayKit (script en la página, no iframe) lee
    ``kalturaStartTime`` de ``window.location.search`` y lo aplica a
    ``sources.startTime``. Funciona en VOD y en live con DVR; el offset
    es segundos desde que arrancó el stream.
    """
    parts = urlsplit(asset_url.strip())
    if not parts.scheme or not parts.netloc:
        raise ValueError(f"WEBTV_URL inválida: {asset_url!r}")
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key != "kalturaStartTime"
    ]
    query.append(("kalturaStartTime", str(max(0, int(round(seconds))))))
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )
