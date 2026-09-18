"""Agregación de avance por día y generador del sitio estático (GitHub Pages)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.config import PIPELINE_ROOT, SessionConfig
from pipeline.roster import load_roster, roster_path
from pipeline.store import (
    find_speech_in_dir,
    out_dir,
    parse_speech_txt,
    speech_relpath,
)


def _coded_speech_ids(config: SessionConfig, day: str, *, dest: Path | None = None) -> set[str]:
    """id_speech presentes en Indicators.csv del día (output de `coding`)."""
    try:
        from pipeline.coding import existing_speech_ids, indicators_csv_path
    except ImportError:
        return set()
    directory = out_dir(config, day, dest=dest)
    return existing_speech_ids(indicators_csv_path(directory))


def _coding_summary_for_speech(
    config: SessionConfig,
    day: str,
    speech_id: str,
    *,
    dest: Path | None = None,
) -> dict[str, Any]:
    """Resumen liviano desde CSV de coding para el dashboard."""
    import csv

    from pipeline.coding import emerging_csv_path, indicators_csv_path

    directory = out_dir(config, day, dest=dest)
    indicators: list[dict[str, str]] = []
    emerging: list[dict[str, str]] = []
    ind_path = indicators_csv_path(directory)
    em_path = emerging_csv_path(directory)
    if ind_path.is_file():
        with ind_path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if str(row.get("id_speech") or "").strip() == speech_id:
                    indicators.append(
                        {
                            "indicator_name": str(row.get("indicator_name") or ""),
                            "code": str(row.get("code") or ""),
                            "option": str(row.get("option") or ""),
                            "model": str(row.get("model") or ""),
                            "date_coded": str(row.get("date_coded") or ""),
                        }
                    )
    if em_path.is_file():
        with em_path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if str(row.get("id_speech") or "").strip() == speech_id:
                    emerging.append(
                        {
                            "emerging_topic": str(row.get("emerging_topic") or ""),
                            "textual_extract": str(row.get("textual_extract") or "")[:240],
                        }
                    )
    model = next((r["model"] for r in indicators if r.get("model")), "")
    dated = next((r["date_coded"] for r in indicators if r.get("date_coded")), "")
    return {
        "status": "ok",
        "analyzed_at": dated,
        "model": model,
        "summary": f"{len(indicators)} indicators · {len(emerging)} emerging",
        "notes": "",
        "fields": {},
        "indicators": indicators,
        "emerging_priorities": emerging,
    }

SOURCE_KEYS = ("pdf_en", "audio_en", "pdf_other", "audio_floor", "video")

REPO_ROOT = PIPELINE_ROOT.parent

_SITE_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Avance análisis UNGA</title>
<style>
:root {
  --bg: #0f1419;
  --panel: #1a222c;
  --border: #2c3846;
  --text: #e7ecf1;
  --muted: #8b9aab;
  --ok: #3dba74;
  --pending: #c9a227;
  --error: #e35d5d;
  --accent: #5b9fd4;
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
.badge.completo { color: var(--ok); border-color: var(--ok); }
.badge.en_progreso { color: var(--pending); border-color: var(--pending); }
.badge.solo_roster { color: var(--accent); border-color: var(--accent); }
.badge.con_errores { color: var(--error); border-color: var(--error); }
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
.timeline {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0.4rem;
  margin-top: 0.65rem;
}
@media (max-width: 640px) {
  .timeline { grid-template-columns: 1fr; }
}
.hito {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.45rem 0.55rem;
  font-size: 0.8rem;
}
.hito .label { color: var(--muted); display: block; margin-bottom: 0.15rem; }
.hito.ok { border-color: color-mix(in srgb, var(--ok) 55%, var(--border)); }
.hito.pending { border-color: color-mix(in srgb, var(--pending) 45%, var(--border)); }
.hito.error { border-color: color-mix(in srgb, var(--error) 55%, var(--border)); }
.status-dot {
  font-weight: 600;
  text-transform: uppercase;
  font-size: 0.72rem;
}
.status-dot.ok { color: var(--ok); }
.status-dot.pending { color: var(--pending); }
.status-dot.error { color: var(--error); }
.detail { padding: 0 0.85rem 0.9rem; font-size: 0.88rem; }
.detail section { margin-top: 0.75rem; }
.detail h3 {
  margin: 0 0 0.35rem;
  font-size: 0.8rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--muted);
}
.detail dl {
  margin: 0;
  display: grid;
  grid-template-columns: 8rem 1fr;
  gap: 0.25rem 0.5rem;
}
.detail dt { color: var(--muted); }
.detail dd { margin: 0; word-break: break-word; }
.sources { margin: 0.35rem 0 0; padding-left: 1.1rem; }
.sources li { margin: 0.15rem 0; }
.prose {
  white-space: pre-wrap;
  background: #0c1015;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.65rem 0.75rem;
  margin: 0.35rem 0 0;
}
a { color: var(--accent); }
.empty { color: var(--muted); padding: 2rem 0; text-align: center; }
</style>
</head>
<body>
<header>
  <h1 id="title">Avance análisis UNGA</h1>
  <p id="subtitle">Cargando…</p>
</header>
<main id="app"></main>
<script>
const STATUS_LABEL = {
  completo: "completo",
  en_progreso: "en progreso",
  solo_roster: "solo roster",
  con_errores: "con errores",
  ok: "ok",
  pending: "pending",
  error: "error",
};

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function linkOrText(url, label) {
  if (!url) return esc(label || "—");
  return `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(label || url)}</a>`;
}

function hitoBox(label, stage) {
  const st = stage?.status || "pending";
  return `<div class="hito ${esc(st)}">
    <span class="label">${esc(label)}</span>
    <span class="status-dot ${esc(st)}">${esc(STATUS_LABEL[st] || st)}</span>
  </div>`;
}

function sourcesList(sources) {
  if (!sources) return "<p>—</p>";
  const items = Object.entries(sources).map(([key, info]) => {
    const available = info && info.available;
    if (!available) return `<li><code>${esc(key)}</code>: no hay</li>`;
    return `<li><code>${esc(key)}</code>: ${linkOrText(info.url, info.filename || info.url)}</li>`;
  });
  return `<ul class="sources">${items.join("")}</ul>`;
}

function speakerDetail(sp) {
  const o = sp.orador || {};
  const d = sp.discurso || {};
  const a = sp.analisis || {};
  const extra = a.fields || {};
  const known = new Set(["summary", "notes", "analyzed_at", "model", "status"]);
  const extraRows = Object.entries(extra)
    .filter(([k]) => !known.has(k))
    .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`)
    .join("");
  return `
    <div class="detail">
      <section>
        <h3>1. Fetch orador</h3>
        <dl>
          <dt>Nombre</dt><dd>${esc(o.name || sp.name)}</dd>
          <dt>Cargo</dt><dd>${esc(o.rank || "—")}</dd>
          <dt>Título</dt><dd>${esc(o.speaker_title || "—")}</dd>
          <dt>País</dt><dd>${esc(sp.country || "—")}</dd>
          <dt>Ficha</dt><dd>${linkOrText(o.ficha_url)}</dd>
          <dt>HTTP</dt><dd>${esc(o.http_status ?? "—")}</dd>
          <dt>Error</dt><dd>${esc(o.error || "—")}</dd>
          <dt>Scraped</dt><dd>${esc(o.scraped_at || "—")}</dd>
        </dl>
      </section>
      <section>
        <h3>2. Fetch discurso</h3>
        <dl>
          <dt>Estado</dt><dd>${esc(STATUS_LABEL[d.status] || d.status || "—")}</dd>
          <dt>Fuente usada</dt><dd>${esc(d.source || "—")}</dd>
          <dt>URL fuente</dt><dd>${linkOrText(d.source_url)}</dd>
          <dt>Transformación</dt><dd>${esc(d.transformation || "—")}</dd>
          <dt>Via</dt><dd>${esc(d.via || "—")}</dd>
          <dt>Elapsed</dt><dd>${d.elapsed_s ? esc(d.elapsed_s) + "s" : "—"}</dd>
          <dt>id_speech</dt><dd>${esc(d.id_speech || "—")}</dd>
          <dt>Chars</dt><dd>${esc(d.chars ?? "—")}</dd>
          <dt>Transcript</dt><dd>${linkOrText(d.transcript_url, d.transcript_rel || "ver")}</dd>
        </dl>
        <p style="margin:0.5rem 0 0;color:var(--muted);font-size:0.8rem">Fuentes en roster</p>
        ${sourcesList(d.sources)}
      </section>
      <section>
        <h3>3. Análisis</h3>
        <dl>
          <dt>Estado</dt><dd>${esc(STATUS_LABEL[a.status] || a.status || "—")}</dd>
          <dt>Fecha</dt><dd>${esc(a.analyzed_at || "—")}</dd>
          <dt>Modelo</dt><dd>${esc(a.model || "—")}</dd>
          ${extraRows}
        </dl>
        <p style="margin:0.65rem 0 0;color:var(--muted);font-size:0.8rem">Summary / coding</p>
        <div class="prose">${esc(a.summary || "—")}</div>
        <p style="margin:0.65rem 0 0;color:var(--muted);font-size:0.8rem">Notes</p>
        <div class="prose">${esc(a.notes || "—")}</div>
        ${Array.isArray(a.indicators) && a.indicators.length ? `
        <p style="margin:0.65rem 0 0;color:var(--muted);font-size:0.8rem">Indicators (${a.indicators.length})</p>
        <ul class="sources">${a.indicators.map(ind =>
          `<li><code>${esc(ind.indicator_name)}</code>: ${esc(ind.code)} ${esc(ind.option)}</li>`
        ).join("")}</ul>` : ""}
        ${Array.isArray(a.emerging_priorities) && a.emerging_priorities.length ? `
        <p style="margin:0.65rem 0 0;color:var(--muted);font-size:0.8rem">Emerging (${a.emerging_priorities.length})</p>
        <ul class="sources">${a.emerging_priorities.map(em =>
          `<li><strong>${esc(em.emerging_topic)}</strong>: ${esc(em.textual_extract || "")}</li>`
        ).join("")}</ul>` : ""}
      </section>
    </div>`;
}

function renderSpeaker(sp) {
  return `<details class="speaker">
    <summary>
      <div class="speaker-head">
        <div>
          <div class="speaker-name">${esc(sp.country || sp.slug)} — ${esc(sp.name || sp.slug)}</div>
          <div class="speaker-sub"><code>${esc(sp.slug)}</code></div>
        </div>
      </div>
      <div class="timeline">
        ${hitoBox("Fetch orador", sp.orador)}
        ${hitoBox("Fetch discurso", sp.discurso)}
        ${hitoBox("Análisis", sp.analisis)}
      </div>
    </summary>
    ${speakerDetail(sp)}
  </details>`;
}

function renderDay(day) {
  const c = day.counts || {};
  const label = STATUS_LABEL[day.status] || day.status;
  return `<details class="day">
    <summary>
      <span class="day-title">${esc(day.day)}</span>
      <span class="day-meta">${esc(c.speakers || 0)} oradores · ${esc(c.speech_ok || 0)}/${esc(c.speakers || 0)} discurso · ${esc(c.analysis_ok || 0)}/${esc(c.speakers || 0)} analizado</span>
      <span class="badge ${esc(day.status)}">${esc(label)}</span>
    </summary>
    <div class="speakers">${(day.speakers || []).map(renderSpeaker).join("")}</div>
  </details>`;
}

async function main() {
  const app = document.getElementById("app");
  const subtitle = document.getElementById("subtitle");
  try {
    const res = await fetch("data/progress.json");
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    document.getElementById("title").textContent =
      `Avance análisis — ${data.name || ("sesión " + data.session)}`;
    subtitle.textContent = `Generado ${data.generated_at || "—"} · ${ (data.days || []).length } día(s)`;
    if (!(data.days || []).length) {
      app.innerHTML = `<p class="empty">Sin días de roster todavía.</p>`;
      return;
    }
    app.innerHTML = data.days.map(renderDay).join("");
  } catch (err) {
    subtitle.textContent = "No se pudo cargar data/progress.json";
    app.innerHTML = `<p class="empty">${esc(err.message || err)}</p>`;
  }
}
main();
</script>
</body>
</html>
"""


def analysis_dir(config: SessionConfig, day: str) -> Path:
    return config.root / "data" / "analysis" / str(config.id) / day


def analysis_path(config: SessionConfig, day: str, slug: str) -> Path:
    return analysis_dir(config, day) / f"{slug}.json"


def write_analysis_snapshot(
    config: SessionConfig,
    *,
    day: str,
    slug: str,
    row: dict[str, Any],
    model: str = "",
    analyzed_at: str | None = None,
) -> Path:
    """Persiste el resultado de análisis para el sitio / Pages."""
    path = analysis_path(config, day, slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = analyzed_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload: dict[str, Any] = {
        "session": config.id,
        "day": day,
        "slug": slug,
        "status": "ok",
        "analyzed_at": stamp,
        "model": model,
        "fields": {str(k): "" if v is None else str(v) for k, v in row.items()},
        "summary": str(row.get("summary") or ""),
        "notes": str(row.get("notes") or ""),
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def load_analysis_snapshot(
    config: SessionConfig, day: str, slug: str
) -> dict[str, Any] | None:
    path = analysis_path(config, day, slug)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def list_roster_days(config: SessionConfig) -> list[str]:
    folder = config.root / "data" / "roster" / str(config.id)
    if not folder.is_dir():
        return []
    days = sorted(p.stem for p in folder.glob("*.json") if p.is_file())
    return days


def _source_summary(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not raw:
        return {"available": False, "url": "", "filename": ""}
    url = str(raw.get("url") or "")
    filename = str(raw.get("filename") or raw.get("entry_id") or "")
    return {
        "available": bool(url or filename),
        "url": url,
        "filename": filename,
    }


def _day_status(
    *,
    speakers: list[dict[str, Any]],
    speech_ok: int,
    analysis_ok: int,
    has_errors: bool,
) -> str:
    n = len(speakers)
    if has_errors:
        return "con_errores"
    if n == 0:
        return "solo_roster"
    if speech_ok == 0:
        return "solo_roster"
    if speech_ok == n and analysis_ok == n:
        return "completo"
    return "en_progreso"


def build_speaker_progress(
    config: SessionConfig,
    entry: dict[str, Any],
    *,
    day: str,
    scraped_at: str,
    dest: Path | None = None,
    repo_root: Path | None = None,
    coded_ids: set[str] | None = None,
) -> dict[str, Any]:
    slug = str(entry.get("slug") or "")
    error = entry.get("error")
    http_status = entry.get("http_status")
    orador_status = "error" if error else "ok"
    orador = {
        "status": orador_status,
        "name": entry.get("name") or "",
        "rank": entry.get("rank") or "",
        "speaker_title": entry.get("speaker_title") or "",
        "ficha_url": entry.get("ficha_url") or config.speaker_url(slug),
        "http_status": http_status,
        "error": error,
        "scraped_at": scraped_at,
    }

    sources_raw = entry.get("sources") or {}
    sources = {key: _source_summary(sources_raw.get(key)) for key in SOURCE_KEYS}

    directory = out_dir(config, day, dest=dest)
    txt_path = find_speech_in_dir(directory, slug) if slug else None
    discurso: dict[str, Any] = {
        "status": "pending",
        "sources": sources,
        "chosen": entry.get("chosen"),
        "source": "",
        "source_url": "",
        "transformation": "",
        "via": "",
        "elapsed_s": None,
        "id_speech": "",
        "chars": None,
        "transcript_url": "",
        "transcript_rel": "",
    }
    if txt_path and txt_path.is_file():
        try:
            speech = parse_speech_txt(txt_path)
            root = repo_root or REPO_ROOT
            rel = speech_relpath(txt_path, repo_root=root)
            transcript_url = ""
            try:
                from pipeline.publish import transcript_url_for

                transcript_url = transcript_url_for(rel)
            except Exception:
                transcript_url = ""
            discurso.update(
                {
                    "status": "ok",
                    "source": speech.source,
                    "source_url": speech.source_url,
                    "transformation": speech.transformation,
                    "via": speech.via,
                    "elapsed_s": speech.elapsed_s or None,
                    "id_speech": speech.id_speech,
                    "chars": len(speech.text),
                    "transcript_url": transcript_url,
                    "transcript_rel": rel,
                }
            )
        except (OSError, ValueError):
            discurso["status"] = "error"
            discurso["error"] = f"no se pudo leer {txt_path.name}"
    elif error and not entry.get("chosen"):
        discurso["status"] = "error"
        discurso["error"] = str(error)

    snap = load_analysis_snapshot(config, day, slug) if slug else None
    speech_id = str(discurso.get("id_speech") or "")
    coded = coded_ids if coded_ids is not None else _coded_speech_ids(config, day, dest=dest)
    if speech_id and speech_id in coded:
        analisis = _coding_summary_for_speech(
            config, day, speech_id, dest=dest
        )
    elif snap:
        analisis = {
            "status": str(snap.get("status") or "ok"),
            "analyzed_at": snap.get("analyzed_at") or "",
            "model": snap.get("model") or "",
            "summary": snap.get("summary") or "",
            "notes": snap.get("notes") or "",
            "fields": snap.get("fields") or {},
            "indicators": snap.get("Indicators") or snap.get("indicators") or [],
            "emerging_priorities": snap.get("Emerging_Priorities")
            or snap.get("emerging_priorities")
            or [],
        }
    else:
        analisis = {
            "status": "pending",
            "analyzed_at": "",
            "model": "",
            "summary": "",
            "notes": "",
            "fields": {},
            "indicators": [],
            "emerging_priorities": [],
        }

    return {
        "slug": slug,
        "country": entry.get("country") or "",
        "name": entry.get("name") or "",
        "orador": orador,
        "discurso": discurso,
        "analisis": analisis,
    }


def build_day_progress(
    config: SessionConfig,
    day: str,
    *,
    dest: Path | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any] | None:
    roster = load_roster(config, day)
    if roster is None:
        return None
    scraped_at = str(roster.get("scraped_at") or "")
    coded_ids = _coded_speech_ids(config, day, dest=dest)
    speakers = [
        build_speaker_progress(
            config,
            entry,
            day=day,
            scraped_at=scraped_at,
            dest=dest,
            repo_root=repo_root,
            coded_ids=coded_ids,
        )
        for entry in (roster.get("speakers") or [])
        if isinstance(entry, dict)
    ]
    speech_ok = sum(1 for s in speakers if s["discurso"]["status"] == "ok")
    analysis_ok = sum(1 for s in speakers if s["analisis"]["status"] == "ok")
    has_errors = any(
        s["orador"]["status"] == "error"
        or s["discurso"]["status"] == "error"
        or s["analisis"]["status"] == "error"
        for s in speakers
    )
    return {
        "day": day,
        "session": config.id,
        "scraped_at": scraped_at,
        "roster_path": str(roster_path(config, day)),
        "status": _day_status(
            speakers=speakers,
            speech_ok=speech_ok,
            analysis_ok=analysis_ok,
            has_errors=has_errors,
        ),
        "counts": {
            "speakers": len(speakers),
            "speech_ok": speech_ok,
            "analysis_ok": analysis_ok,
        },
        "speakers": speakers,
    }


def build_session_progress(
    config: SessionConfig,
    *,
    dest: Path | None = None,
    repo_root: Path | None = None,
    days: list[str] | None = None,
) -> dict[str, Any]:
    selected = days if days is not None else list_roster_days(config)
    day_payloads: list[dict[str, Any]] = []
    for day in selected:
        payload = build_day_progress(
            config, day, dest=dest, repo_root=repo_root
        )
        if payload is not None:
            day_payloads.append(payload)
    # Newest first for the accordion
    day_payloads.sort(key=lambda d: d["day"], reverse=True)
    return {
        "session": config.id,
        "name": config.name,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "days": day_payloads,
    }


def write_progress_site(
    progress: dict[str, Any],
    docs_dir: Path,
) -> tuple[Path, Path]:
    docs_dir.mkdir(parents=True, exist_ok=True)
    data_dir = docs_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    json_path = data_dir / "progress.json"
    html_path = docs_dir / "index.html"
    json_path.write_text(
        json.dumps(progress, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    html_path.write_text(_SITE_HTML, encoding="utf-8")
    (docs_dir / ".nojekyll").write_text("", encoding="utf-8")
    return html_path, json_path


def generate_progress_site(
    config: SessionConfig,
    *,
    docs_dir: Path | None = None,
    dest: Path | None = None,
    repo_root: Path | None = None,
    days: list[str] | None = None,
) -> dict[str, Any]:
    root = repo_root or REPO_ROOT
    out = docs_dir or (root / "docs")
    progress = build_session_progress(
        config, dest=dest, repo_root=root, days=days
    )
    html_path, json_path = write_progress_site(progress, out)
    print(
        json.dumps(
            {
                "session": config.id,
                "days": len(progress["days"]),
                "html": str(html_path),
                "json": str(json_path),
            }
        ),
        file=sys.stderr,
    )
    return progress
