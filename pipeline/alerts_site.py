"""Sitio estático de keywords detectadas en vivo (GitHub Pages)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.keyword_journal import load_highlights, load_jsonl, merge_records
from pipeline.config import SessionConfig
from pipeline.progress import REPO_ROOT, list_roster_days
from pipeline.roster import load_roster

_SITE_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Keywords UNGA</title>
<style>
:root {
  --bg: #0f1419;
  --panel: #1a222c;
  --border: #2c3846;
  --text: #e7ecf1;
  --muted: #8b9aab;
  --ok: #3dba74;
  --pending: #c9a227;
  --accent: #5b9fd4;
  --chip: #243044;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: "IBM Plex Sans", "Segoe UI", system-ui, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.45;
}
header {
  padding: 1.5rem 1.25rem 1rem;
  border-bottom: 1px solid var(--border);
}
header h1 { margin: 0 0 0.35rem; font-size: 1.35rem; font-weight: 600; }
header p { margin: 0; color: var(--muted); font-size: 0.9rem; }
.nav { margin-top: 0.55rem !important; }
.nav a { color: var(--accent); }
main { max-width: 960px; margin: 0 auto; padding: 1rem 1.25rem 3rem; }
.day {
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel);
  margin-bottom: 0.75rem;
  overflow: hidden;
}
.day > summary {
  list-style: none;
  cursor: pointer;
  padding: 0.9rem 1rem;
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem 1rem;
  align-items: center;
  justify-content: space-between;
}
.day > summary::-webkit-details-marker { display: none; }
.day-title { font-weight: 600; }
.day-meta { color: var(--muted); font-size: 0.85rem; }
.badge {
  display: inline-block;
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  padding: 0.2rem 0.5rem;
  border-radius: 999px;
  border: 1px solid var(--border);
}
.badge.con_hits { color: var(--ok); border-color: var(--ok); }
.badge.sin_hits { color: var(--muted); border-color: var(--border); }
.speakers { padding: 0 0.75rem 0.75rem; border-top: 1px solid var(--border); }
.speaker {
  border: 1px solid var(--border);
  border-radius: 6px;
  margin-top: 0.65rem;
  background: #12181f;
}
.speaker > summary {
  list-style: none;
  cursor: pointer;
  padding: 0.75rem 0.85rem;
}
.speaker > summary::-webkit-details-marker { display: none; }
.speaker-head {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem 0.85rem;
  align-items: baseline;
  justify-content: space-between;
}
.speaker-name { font-weight: 600; }
.speaker-sub { color: var(--muted); font-size: 0.85rem; }
.chips { display: flex; flex-wrap: wrap; gap: 0.3rem; margin-top: 0.45rem; }
.chip {
  font-size: 0.75rem;
  background: var(--chip);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0.12rem 0.5rem;
}
.detail { padding: 0 0.85rem 0.9rem; font-size: 0.88rem; }
.hit {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.65rem 0.75rem;
  margin-top: 0.55rem;
  background: #0c1015;
}
.hit-meta { color: var(--muted); font-size: 0.8rem; margin: 0 0 0.35rem; }
.hit-kw { font-weight: 600; color: var(--accent); }
.context {
  margin: 0;
  white-space: pre-wrap;
}
a { color: var(--accent); }
.empty { color: var(--muted); padding: 2rem 0; text-align: center; }
</style>
</head>
<body>
<header>
  <h1 id="title">Keywords en vivo</h1>
  <p id="subtitle">Cargando…</p>
  <p class="nav"><a href="index.html">Análisis</a> · <a href="alerts.html">Keywords</a></p>
</header>
<main id="app"></main>
<script>
function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function linkOrText(url, label) {
  if (!url) return "";
  return ` · <a href="${esc(url)}" target="_blank" rel="noopener">${esc(label || "link")}</a>`;
}

function renderHit(hit) {
  const when = hit.when_ny || hit.timestamp || "—";
  const kw = esc(hit.keyword || "");
  let extra = "";
  if (hit.timestamp_reliable && hit.player) extra += " · " + esc(hit.player);
  extra += linkOrText(hit.webtv_url, "UN Web TV");
  extra += linkOrText(hit.watch_url, "YouTube");
  return `<article class="hit">
    <p class="hit-meta"><span class="hit-kw">${kw}</span> · ${esc(when)}${extra}</p>
    <p class="context">${esc(hit.context || "")}</p>
  </article>`;
}

function renderSpeaker(sp) {
  const hits = sp.hits || [];
  const kws = [...new Set(hits.map(h => h.keyword).filter(Boolean))];
  const chips = kws.map(k => `<span class="chip">${esc(k)}</span>`).join("");
  const body = hits.length
    ? hits.map(renderHit).join("")
    : `<p class="empty" style="padding:0.5rem 0 0">Sin keywords en este discurso.</p>`;
  return `<details class="speaker">
    <summary>
      <div class="speaker-head">
        <span class="speaker-name">${esc(sp.name || "unknown")}</span>
        <span class="speaker-sub">${esc(sp.country || "")}${sp.title ? " · " + esc(sp.title) : ""} · ${hits.length} hit(s)</span>
      </div>
      ${chips ? `<div class="chips">${chips}</div>` : ""}
    </summary>
    <div class="detail">${body}</div>
  </details>`;
}

function renderDay(day) {
  const c = day.counts || {};
  const status = day.status || (c.hits ? "con_hits" : "sin_hits");
  const label = status === "con_hits" ? "con hits" : "sin hits";
  return `<details class="day">
    <summary>
      <span class="day-title">${esc(day.day)}</span>
      <span class="day-meta">${esc(c.speakers || 0)} oradores · ${esc(c.with_hits || 0)} con keyword · ${esc(c.hits || 0)} hit(s)</span>
      <span class="badge ${esc(status)}">${esc(label)}</span>
    </summary>
    <div class="speakers">${(day.speakers || []).map(renderSpeaker).join("")}</div>
  </details>`;
}

async function main() {
  const app = document.getElementById("app");
  const subtitle = document.getElementById("subtitle");
  try {
    const res = await fetch("data/alerts.json");
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    document.getElementById("title").textContent =
      `Keywords en vivo — ${data.name || ("sesión " + data.session)}`;
    subtitle.textContent = `Generado ${data.generated_at || "—"} · ${ (data.days || []).length } día(s)`;
    if (!(data.days || []).length) {
      app.innerHTML = `<p class="empty">Sin detecciones todavía. El monitor escribe logs/keywords.jsonl; después corré alerts-site.</p>`;
      return;
    }
    app.innerHTML = data.days.map(renderDay).join("");
  } catch (err) {
    subtitle.textContent = "No se pudo cargar data/alerts.json";
    app.innerHTML = `<p class="empty">${esc(err.message || err)}</p>`;
  }
}
main();
</script>
</body>
</html>
"""


def alerts_data_dir(config: SessionConfig) -> Path:
    return config.root / "data" / "alerts" / str(config.id)


def _norm_name(name: str) -> str:
    return " ".join((name or "").casefold().split())


def _format_player(seconds: float | None, reliable: bool) -> str:
    if not reliable or seconds is None:
        return ""
    total = max(0, int(round(seconds)))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _when_ny(timestamp: str) -> str:
    raw = (timestamp or "").strip()
    if not raw:
        return ""
    try:
        if raw.endswith("Z"):
            stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            stamp = datetime.fromisoformat(raw)
    except ValueError:
        return raw
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    from zoneinfo import ZoneInfo

    return stamp.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M:%S %Z")


def _public_hit(record: dict) -> dict[str, Any]:
    seconds = record.get("video_seconds")
    try:
        video_seconds = float(seconds) if seconds is not None else None
    except (TypeError, ValueError):
        video_seconds = None
    reliable = bool(record.get("timestamp_reliable", True))
    return {
        "keyword": str(record.get("keyword") or ""),
        "context": str(record.get("context") or ""),
        "timestamp": str(record.get("timestamp") or ""),
        "when_ny": _when_ny(str(record.get("timestamp") or "")),
        "player": _format_player(video_seconds, reliable),
        "timestamp_reliable": reliable,
        "watch_url": str(record.get("watch_url") or ""),
        "webtv_url": str(record.get("webtv_url") or ""),
        "speaker_title": str(record.get("speaker_title") or ""),
    }


def load_snapshot_records(config: SessionConfig) -> list[dict]:
    folder = alerts_data_dir(config)
    if not folder.is_dir():
        return []
    records: list[dict] = []
    for path in sorted(folder.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(raw, list):
            records.extend(item for item in raw if isinstance(item, dict))
        elif isinstance(raw, dict) and isinstance(raw.get("hits"), list):
            records.extend(item for item in raw["hits"] if isinstance(item, dict))
    return records


def collect_records(
    config: SessionConfig,
    *,
    log_dir: Path | None = None,
) -> list[dict]:
    groups: list[list[dict]] = [load_snapshot_records(config)]
    if log_dir is not None and log_dir.is_dir():
        groups.append(load_jsonl(log_dir / "keywords.jsonl"))
        groups.append(load_highlights(log_dir))
    return merge_records(*groups)


def _match_speaker(name: str, roster_names: list[tuple[str, dict]]) -> dict | None:
    needle = _norm_name(name)
    if not needle:
        return None
    for key, entry in roster_names:
        if key == needle:
            return entry
    if len(needle) < 8:
        return None
    for key, entry in roster_names:
        if needle in key or key in needle:
            return entry
    return None


def write_snapshots(config: SessionConfig, records: list[dict]) -> list[Path]:
    by_day: dict[str, list[dict]] = {}
    for record in records:
        day = str(record.get("day") or "")
        if not day:
            continue
        by_day.setdefault(day, []).append(record)
    written: list[Path] = []
    root = alerts_data_dir(config)
    for day, hits in by_day.items():
        path = root / f"{day}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "session": config.id,
            "day": day,
            "hits": hits,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(path)
    return written


def build_alerts_payload(
    config: SessionConfig,
    records: list[dict],
    *,
    days: list[str] | None = None,
) -> dict[str, Any]:
    by_day: dict[str, list[dict]] = {}
    for record in records:
        day = str(record.get("day") or "")
        if not day:
            continue
        by_day.setdefault(day, []).append(record)

    roster_days = list_roster_days(config)
    selected = set(days) if days is not None else (set(roster_days) | set(by_day))
    day_payloads: list[dict[str, Any]] = []
    for day in sorted(selected, reverse=True):
        roster = load_roster(config, day) or {}
        roster_speakers = list(roster.get("speakers") or [])
        roster_pairs = [
            (_norm_name(str(entry.get("name") or "")), entry)
            for entry in roster_speakers
            if entry.get("name")
        ]
        hits = by_day.get(day) or []
        assigned: dict[str, list[dict]] = {}
        extras: dict[str, list[dict]] = {}
        for hit in hits:
            entry = _match_speaker(str(hit.get("speaker") or ""), roster_pairs)
            if entry is not None:
                slug = str(entry.get("slug") or entry.get("name") or "")
                assigned.setdefault(slug, []).append(hit)
            else:
                key = _norm_name(str(hit.get("speaker") or "")) or "unknown"
                extras.setdefault(key, []).append(hit)

        speakers: list[dict[str, Any]] = []
        for entry in roster_speakers:
            slug = str(entry.get("slug") or "")
            speaker_hits = assigned.get(slug, [])
            speakers.append(
                {
                    "slug": slug,
                    "name": str(entry.get("name") or ""),
                    "country": str(entry.get("country") or ""),
                    "title": str(entry.get("rank") or entry.get("speaker_title") or ""),
                    "hits": [_public_hit(item) for item in speaker_hits],
                }
            )
        for key, extra_hits in extras.items():
            first = extra_hits[0]
            speakers.append(
                {
                    "slug": f"live:{key}",
                    "name": str(first.get("speaker") or "unknown"),
                    "country": "",
                    "title": str(first.get("speaker_title") or ""),
                    "hits": [_public_hit(item) for item in extra_hits],
                }
            )
        if not speakers and not hits:
            continue
        n_hits = sum(len(sp["hits"]) for sp in speakers)
        n_with = sum(1 for sp in speakers if sp["hits"])
        day_payloads.append(
            {
                "day": day,
                "status": "con_hits" if n_hits else "sin_hits",
                "counts": {
                    "speakers": len(speakers),
                    "with_hits": n_with,
                    "hits": n_hits,
                },
                "speakers": speakers,
            }
        )
    return {
        "session": config.id,
        "name": config.name,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "days": day_payloads,
    }


def write_alerts_site(payload: dict[str, Any], docs_dir: Path) -> tuple[Path, Path]:
    docs_dir.mkdir(parents=True, exist_ok=True)
    data_dir = docs_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    json_path = data_dir / "alerts.json"
    html_path = docs_dir / "alerts.html"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    html_path.write_text(_SITE_HTML, encoding="utf-8")
    (docs_dir / ".nojekyll").write_text("", encoding="utf-8")
    return html_path, json_path


def generate_alerts_site(
    config: SessionConfig,
    *,
    docs_dir: Path | None = None,
    log_dir: Path | None = None,
    days: list[str] | None = None,
    snapshot: bool = False,
) -> dict[str, Any]:
    records = collect_records(config, log_dir=log_dir)
    if snapshot and records:
        write_snapshots(config, records)
    payload = build_alerts_payload(config, records, days=days)
    out = docs_dir or (REPO_ROOT / "docs")
    html_path, json_path = write_alerts_site(payload, out)
    print(
        json.dumps(
            {
                "session": config.id,
                "days": len(payload["days"]),
                "hits": sum(int(d["counts"]["hits"]) for d in payload["days"]),
                "html": str(html_path),
                "json": str(json_path),
            },
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )
    return payload
