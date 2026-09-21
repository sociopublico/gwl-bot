from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

logger = logging.getLogger(__name__)

DEFAULT_PARTNER_ID = "2503451"
# Listings 24/7 (UNTV) tienen release_timestamp de hace meses; un día UNGA no pasa de esto.
MAX_LIVE_ORIGIN_SECONDS = 36 * 3600
_ASSET_SLUG_RE = re.compile(r"/asset/[^/]+/(k[0-9][0-9a-z]+)/?", re.I)


@dataclass(frozen=True)
class WebtvClock:
    entry_id: str
    dvr: bool
    elapsed_seconds: float | None
    broadcast_start: float | None


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


def entry_id_from_webtv_url(url: str) -> str | None:
    """Drupal usa k10h1p03zp; Kaltura usa 1_0h1p03zp (misma id, con guion bajo)."""
    path = urlsplit(url.strip()).path
    match = _ASSET_SLUG_RE.search(path)
    if not match:
        return None
    slug = match.group(1)
    if len(slug) < 3 or slug[1] != "1":
        return None
    return "1_" + slug[2:]


def _json_get(url: str, timeout: float = 15.0) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "gwl-bot/webtv"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    if not isinstance(payload, dict):
        raise ValueError("Kaltura did not return an object")
    return payload


def _widget_ks(partner_id: str) -> str:
    data = _json_get(
        "https://cdnapisec.kaltura.com/api_v3/index.php"
        f"?service=session&action=startWidgetSession&widgetId=_{partner_id}&format=1"
    )
    ks = data.get("ks")
    if not ks:
        raise ValueError("Kaltura widget session has no ks")
    return str(ks)


def probe_webtv_clock(
    asset_url: str,
    *,
    now: float,
    partner_id: str = DEFAULT_PARTNER_ID,
) -> WebtvClock | None:
    """Lee DVR y elapsed del live de Kaltura. None si no se pudo consultar."""
    entry_id = entry_id_from_webtv_url(asset_url)
    if not entry_id:
        logger.warning("WEBTV_URL has no UN asset slug: %s", asset_url)
        return None
    try:
        ks = _widget_ks(partner_id)
        entry = _json_get(
            "https://cdnapisec.kaltura.com/api_v3/index.php"
            f"?service=liveStream&action=get&ks={ks}&entryId={entry_id}&format=1"
        )
    except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("UN Web TV clock probe failed | entry=%s | %s", entry_id, exc)
        return None
    if entry.get("code"):
        logger.warning(
            "UN Web TV clock probe failed | entry=%s | %s",
            entry_id,
            entry.get("message") or entry.get("code"),
        )
        return None

    dvr = bool(entry.get("dvrStatus"))
    start_raw = entry.get("currentBroadcastStartTime")
    try:
        start = float(start_raw) if start_raw else None
    except (TypeError, ValueError):
        start = None
    elapsed = None
    if start and start > 0:
        age = now - start
        if 0 <= age <= MAX_LIVE_ORIGIN_SECONDS:
            elapsed = age
        else:
            start_iso = datetime.fromtimestamp(start, tz=timezone.utc).isoformat()
            logger.warning(
                "UN Web TV broadcast start is not usable as origin | entry=%s | "
                "start=%s | age=%.0fh (max %.0fh)",
                entry_id,
                start_iso,
                age / 3600,
                MAX_LIVE_ORIGIN_SECONDS / 3600,
            )
    return WebtvClock(
        entry_id=entry_id,
        dvr=dvr,
        elapsed_seconds=elapsed,
        broadcast_start=start,
    )
