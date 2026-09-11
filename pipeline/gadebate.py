from __future__ import annotations

import json
import re
from html import unescape

from pipeline.config import SessionConfig
from pipeline.http import HttpError, fetch
from pipeline.models import FileRef, SpeakerPage

_SESSION_TAIL = re.compile(
    r"\s*[—–-]\s*\d+(?:st|nd|rd|th)\s+session.*$",
    re.I,
)


def strip_tags(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", unescape(raw))
    return re.sub(r"\s+", " ", text).strip()


def _field(html: str, name: str) -> str:
    match = re.search(
        rf'field--name-{re.escape(name)}[\s\S]{{0,800}}?<div class="field__item"[^>]*>([\s\S]*?)</div>',
        html,
    )
    return strip_tags(match.group(1)) if match else ""


def _abs(config: SessionConfig, href: str) -> str:
    href = href.strip()
    if href.startswith("http"):
        return href
    return config.gadebate_base + (href if href.startswith("/") else "/" + href)


def _pdfs_from_html(config: SessionConfig, html: str) -> list[FileRef]:
    match = re.search(
        r"Full statement([\s\S]*?)(?:<h3>Audio</h3>|<h3>Photo</h3>|Right of reply)",
        html,
        re.I,
    )
    chunk = match.group(1) if match else ""
    pdfs: list[FileRef] = []
    for lab, href in re.findall(
        r'custom-statement-file-label">([\s\S]*?)</div>[\s\S]*?href="([^"]+\.pdf)"',
        chunk,
        re.I,
    ):
        url = _abs(config, href)
        pdfs.append(FileRef(label=strip_tags(lab), url=url, filename=url.rsplit("/", 1)[-1]))
    if not pdfs:
        for href in re.findall(r'href="([^"]+\.pdf)"', chunk, re.I):
            url = _abs(config, href)
            pdfs.append(FileRef(label="", url=url, filename=url.rsplit("/", 1)[-1]))
    return pdfs


def _audios_from_html(html: str) -> list[FileRef]:
    seen: dict[str, FileRef] = {}
    for href, lang, label in re.findall(
        r'<option value="\s*(https://[^"]+\.mp3)"[^>]*data-language-code="([^"]*)"[^>]*>\s*([^<]+)</option>',
        html,
        re.I,
    ):
        url = href.strip()
        seen[url] = FileRef(
            label=strip_tags(label).lower(),
            url=url,
            filename=url.rsplit("/", 1)[-1],
            lang=lang.lower(),
        )
    return list(seen.values())


def _name_from_html(html: str) -> str:
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    if match:
        raw = match.group(1).replace("&amp;", "&")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
        desc = str(data.get("description") or "")
        name = _SESSION_TAIL.sub("", desc).strip()
        name = re.sub(r"^(His|Her)\s+Excellency\s+", "", name, flags=re.I).strip()
        if name:
            return name
    alt = re.search(r'alt="Portrait of (?:His|Her) Excellency ([^"(]+)', html)
    return alt.group(1).strip() if alt else ""


def _pick_pdfs(pdfs: list[FileRef], orig_hint: str) -> tuple[FileRef | None, FileRef | None]:
    en = next(
        (
            p
            for p in pdfs
            if "english" in p.label.lower() or p.filename.lower().endswith("_en.pdf")
        ),
        None,
    )
    hint = orig_hint.lower().strip()
    orig = None
    if hint:
        orig = next(
            (p for p in pdfs if p.filename.lower() == hint or hint in p.filename.lower()),
            None,
        )
    if orig is None:
        orig = next((p for p in pdfs if "as delivered" in p.label.lower()), None)
    if orig is None:
        orig = next(
            (
                p
                for p in pdfs
                if p is not en and not p.filename.lower().endswith("_en.pdf")
            ),
            None,
        )
    if orig is en:
        orig = None
    return en, orig


def _pick_audios(audios: list[FileRef]) -> tuple[FileRef | None, FileRef | None]:
    en = next(
        (
            a
            for a in audios
            if a.lang == "en" or a.filename.upper().endswith("_EN.mp3")
        ),
        None,
    )
    floor = next(
        (
            a
            for a in audios
            if a.filename.upper().endswith("_FL.mp3") or a.label == "original language"
        ),
        None,
    )
    return en, floor


def parse_speaker_page(config: SessionConfig, url: str, html: str) -> SpeakerPage:
    slug = url.rstrip("/").split("/")[-1]
    title = ""
    t = re.search(r"<title>([^<]+)</title>", html, re.I)
    if t:
        title = strip_tags(t.group(1)).split("|")[0].strip()
    country = _field(html, "field-speaker-list") or title
    pdfs = _pdfs_from_html(config, html)
    audios = _audios_from_html(html)
    pdf_en, pdf_other = _pick_pdfs(pdfs, _field(html, "field-language-neutral-statement"))
    audio_en, audio_floor = _pick_audios(audios)
    datetime_attr = ""
    d = re.search(r'datetime="([^"]+)"', html)
    if d:
        datetime_attr = d.group(1)
    kaltura = None
    km = re.search(r'data-entryid="([^"]+)"', html)
    if km:
        kaltura = km.group(1)
    partner = None
    pm = re.search(r'data-partnerid="([^"]+)"', html, re.I)
    if pm:
        partner = pm.group(1)
    else:
        pm = re.search(r"kaltura\.com/p/(\d+)", html, re.I)
        if pm:
            partner = pm.group(1)
    return SpeakerPage(
        slug=slug,
        url=url,
        country=country,
        name=_name_from_html(html),
        rank=_field(html, "field-speaker-function-2") or _field(html, "field-speaker-name"),
        speaker_title=_field(html, "field-speaker-title"),
        speech_date=datetime_attr[:10] if datetime_attr else "",
        pdfs=pdfs,
        audios=audios,
        pdf_en=pdf_en,
        pdf_other=pdf_other,
        audio_en=audio_en,
        audio_floor=audio_floor,
        video_entry_id=kaltura,
        video_partner_id=partner,
    )


def scrape_speaker(config: SessionConfig, slug: str) -> SpeakerPage:
    url = config.speaker_url(slug)
    try:
        status, _, body = fetch(url, user_agent=config.user_agent)
        page = parse_speaker_page(config, url, body.decode("utf-8", "replace"))
        page.http_status = status
        return page
    except HttpError as exc:
        return SpeakerPage(
            slug=slug,
            url=url,
            country="",
            name="",
            rank="",
            speaker_title="",
            speech_date="",
            error=str(exc),
            http_status=exc.status,
        )


def slugs_from_archive_html(config: SessionConfig, html: str) -> list[str]:
    prefix = f"/en/{config.id}/"
    found = []
    seen: set[str] = set()
    for href in re.findall(r'href="([^"]+)"', html):
        if prefix not in href:
            continue
        slug = href.split(prefix, 1)[-1].strip("/").split("?")[0]
        if not slug or slug in seen or "/" in slug:
            continue
        seen.add(slug)
        found.append(slug)
    return found


def refresh_slugs_from_archive(config: SessionConfig) -> list[str]:
    _, _, body = fetch(config.archive_url(), user_agent=config.user_agent)
    html = body.decode("utf-8", "replace")
    slugs = slugs_from_archive_html(config, html)
    if not slugs:
        raise RuntimeError(
            f"El archive no devolvió fichas /en/{config.id}/ "
            f"(el sitio a veces está en mantenimiento). "
            f"Usá el slug_file de {config.name}."
        )
    return slugs
