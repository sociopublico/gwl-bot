from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from pipeline.alerts import DEFAULT_KEYWORDS_PATH, print_alert_report
from pipeline.config import load_session, load_slugs, write_slugs
from pipeline.gadebate import refresh_slugs_from_archive
from pipeline.run import FetchItem, fetch_speeches, select_slugs

_KNOWN_SOURCES = {"pdf_en", "audio_en", "pdf_other", "video"}


def _parse_reextract(raw: str) -> set[str]:
    return {part.strip() for part in raw.split(",") if part.strip()}


def _apply_sources(config, raw: str):
    sources = tuple(part.strip() for part in raw.split(",") if part.strip())
    unknown = [s for s in sources if s not in _KNOWN_SOURCES]
    if unknown:
        raise ValueError(f"sources desconocidos: {unknown}")
    return replace(config, sources=sources)


def _print_extract_item(item: FetchItem, *, metadata_only: bool = False) -> str:
    page = item.page
    if page.error:
        print(f"FAIL {page.slug} {page.error}", file=sys.stderr)
        return "error"
    if item.skip == "already extracted":
        print(f"SKIP {page.slug} {item.skip}", file=sys.stderr)
        return "skipped"
    if item.speech:
        print(
            f"OK {page.slug} source={item.speech.source} via={item.via or '?'} "
            f"chars={len(item.speech.text)} elapsed={item.elapsed_s:.1f}s",
            file=sys.stderr,
        )
        return "ok"
    if item.skip and not metadata_only:
        print(f"SKIP {page.slug} {item.skip}", file=sys.stderr)
        return "skipped"
    return "other"


def _summarize_extract(results: list[FetchItem], *, metadata_only: bool = False) -> int:
    ok = skipped = errors = 0
    for item in results:
        kind = _print_extract_item(item, metadata_only=metadata_only)
        if kind == "ok":
            ok += 1
        elif kind == "skipped":
            skipped += 1
        elif kind == "error":
            errors += 1
    print(
        json.dumps(
            {"fetched": len(results), "ok": ok, "skipped": skipped, "errors": errors}
        ),
        file=sys.stderr,
    )
    return 0 if errors == 0 else 2


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--session",
        default="80",
        help="80, 81, unga80, unga81, o ruta a un .toml",
    )
    common.add_argument(
        "--out",
        default="",
        help="Directorio de salida (default: pipeline/out)",
    )
    parser = argparse.ArgumentParser(
        prog="pipeline",
        description="Discursos UNGA: scrape y extracción. Sesión 80 o 81 por config.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="Listar slugs de la sesión", parents=[common])
    refresh = sub.add_parser(
        "refresh-slugs",
        help="Intentar rellenar el slug_file desde gadebate sessions-archive",
        parents=[common],
    )
    refresh.add_argument("--write", action="store_true", help="Sobrescribir el slug_file")

    proto = sub.add_parser(
        "refresh-protocol",
        help="Bajar el PDF de Protocolo UN (HS/HG/MFA) y actualizar speaker_level/género",
        parents=[common],
    )
    proto.add_argument(
        "--pdf",
        default="",
        help="PDF local (default: baja hspmfmlist_0.pdf de un.org)",
    )

    roster_p = sub.add_parser(
        "roster",
        help="Scrape fichas del día (laptop) y escribir JSON con URLs de pdf/audio/video",
        parents=[common],
    )
    roster_p.add_argument("--day", required=True, help="YYYY-MM-DD")
    roster_p.add_argument("--slug", default="", help="Una sola ficha")
    roster_p.add_argument("--limit", type=int, default=0, help="Máximo de oradores")
    roster_p.add_argument(
        "--speakers-txt",
        default="",
        help="Además, exportar nombres a este archivo (p.ej. speakers.txt del monitor)",
    )

    export_sp = sub.add_parser(
        "export-speakers",
        help="Exportar nombres del roster JSON a speakers.txt (monitor de alertas)",
        parents=[common],
    )
    export_sp.add_argument("--day", required=True, help="YYYY-MM-DD")
    export_sp.add_argument(
        "--speakers-txt",
        default="speakers.txt",
        help="Archivo de salida (default: speakers.txt en el cwd)",
    )

    speakers_p = sub.add_parser(
        "speakers",
        help="Bajar la lista pública de e-speakers y escribir speakers.txt (monitor de alertas)",
        parents=[common],
    )
    speakers_p.add_argument(
        "--url",
        required=True,
        help="URL o hash de e-speakers (p.ej. https://e-speakers.e-delegate.un.org/<id>)",
    )
    speakers_p.add_argument(
        "--day",
        default="",
        help="YYYY-MM-DD (default: todos los días con nombre en la lista)",
    )
    speakers_p.add_argument(
        "--speakers-txt",
        default="speakers.txt",
        help="Archivo de salida (default: speakers.txt en el cwd)",
    )

    extract_p = sub.add_parser(
        "extract",
        help="Bajar y transcribir desde un roster JSON (sin scrape de gadebate)",
        parents=[common],
    )
    extract_p.add_argument("--day", required=True, help="YYYY-MM-DD")
    extract_p.add_argument("--slug", default="", help="Una sola ficha")
    extract_p.add_argument("--limit", type=int, default=0)
    extract_p.add_argument(
        "--sources",
        default="",
        help="Cascada, p.ej. pdf_en,audio_en,pdf_other",
    )
    extract_p.add_argument(
        "--skip-existing",
        action="store_true",
        help="No re-extraer slugs que ya tienen .txt en inglés",
    )
    extract_p.add_argument(
        "--reextract",
        default="",
        help="Slugs a re-extraer aunque exista .txt inglés (coma-separados). Combina con --skip-existing.",
    )

    analyze_p = sub.add_parser(
        "analyze",
        help="Mandar discursos a Claude y append filas a la pestaña Analysis",
        parents=[common],
    )
    analyze_p.add_argument("--day", required=True, help="YYYY-MM-DD")
    analyze_p.add_argument("--slug", default="", help="Una sola ficha")
    analyze_p.add_argument(
        "--prompt",
        default="",
        help="Markdown del prompt (default: pipeline/data/analyze-prompt.md)",
    )
    analyze_p.add_argument(
        "--dry-run",
        action="store_true",
        help="No llamar a Claude ni escribir Sheets; mostrar qué se haría",
    )

    fetch_p = sub.add_parser(
        "fetch",
        help="Bajar fichas y extraer texto (pdf_en → audio_en → pdf_other traducido → video)",
        parents=[common],
    )
    fetch_p.add_argument("--day", default="", help="YYYY-MM-DD (día del discurso / UN Journal)")
    fetch_p.add_argument("--slug", default="", help="Una sola ficha, p.ej. brazil")
    fetch_p.add_argument("--limit", type=int, default=0, help="Máximo de oradores")
    fetch_p.add_argument(
        "--metadata-only",
        action="store_true",
        help="Solo scrape de la ficha, no baja PDFs",
    )
    fetch_p.add_argument(
        "--sources",
        default="",
        help="Cascada, p.ej. pdf_en,audio_en,pdf_other (default: la del TOML; video es último recurso)",
    )
    fetch_p.add_argument(
        "--skip-existing",
        action="store_true",
        help="No re-extraer slugs que ya tienen .txt en inglés",
    )
    fetch_p.add_argument(
        "--reextract",
        default="",
        help="Slugs a re-extraer aunque exista .txt inglés (coma-separados). Combina con --skip-existing.",
    )

    alerts_p = sub.add_parser(
        "alerts",
        help="Simular cuántos mails de alerta saldrían por día sobre discursos extraídos",
        parents=[common],
    )
    alerts_p.add_argument(
        "--keywords",
        default="",
        help=f"TOML de keywords (default: {DEFAULT_KEYWORDS_PATH})",
    )
    alerts_p.add_argument(
        "--all-languages",
        action="store_true",
        help="Incluir discursos que no están en inglés",
    )
    alerts_p.add_argument("--json", action="store_true", help="Salida JSON")

    pub = sub.add_parser(
        "publish",
        help="Versionar txt en GitHub y/o append a Google Sheets (Metadata)",
        parents=[common],
    )
    pub.add_argument("--day", default="", help="YYYY-MM-DD")
    pub.add_argument("--slug", default="", help="Una sola ficha")
    pub.add_argument(
        "--github",
        action="store_true",
        help="git add + commit + push de los .txt del día",
    )
    pub.add_argument(
        "--sheet",
        action="store_true",
        help="Append idempotente a la pestaña Metadata (service account)",
    )
    pub.add_argument(
        "--no-csv",
        action="store_true",
        help="No escribir metadata.csv local",
    )

    coding_p = sub.add_parser(
        "coding",
        help="Codear discursos del día con Claude → Indicators.csv + Emerging_Priorities.csv",
        parents=[common],
    )
    coding_p.add_argument("--day", required=True, help="YYYY-MM-DD")
    coding_p.add_argument("--slug", default="", help="Una sola ficha")
    coding_p.add_argument(
        "--prompt",
        default="",
        help="Metodología markdown (default: claude-prompt.md en la raíz del repo)",
    )
    coding_p.add_argument(
        "--dry-run",
        action="store_true",
        help="No llamar a Claude ni escribir CSV; mostrar las tandas",
    )
    coding_p.add_argument(
        "--force",
        action="store_true",
        help="Recodear id_speech que ya están en Indicators.csv del día",
    )

    coding_sheet_p = sub.add_parser(
        "coding-sheet",
        help="Append idempotente de los CSV de coding a Indicators y Emerging_Priorities",
        parents=[common],
    )
    coding_sheet_p.add_argument("--day", required=True, help="YYYY-MM-DD")
    coding_sheet_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Contar append vs skip; no escribir el spreadsheet",
    )

    site = sub.add_parser(
        "progress-site",
        help="Generar sitio estático de avance (docs/ para GitHub Pages)",
        parents=[common],
    )
    site.add_argument(
        "--docs",
        default="",
        help="Directorio de salida (default: docs/ en la raíz del repo)",
    )
    site.add_argument(
        "--day",
        default="",
        help="Solo un día YYYY-MM-DD (default: todos los roster de la sesión)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = load_session(args.session)
    dest = Path(args.out) if args.out else None

    if args.cmd == "list":
        slugs = load_slugs(config)
        print(
            f"{config.name} id={config.id} slugs={len(slugs)} "
            f"fechas={', '.join(config.debate_dates) or '—'}",
            file=sys.stderr,
        )
        for slug in slugs:
            print(slug)
        if not slugs:
            print(
                "Sin slugs. Para 81: esperá el archive o corre refresh-slugs.",
                file=sys.stderr,
            )
        return 0

    if args.cmd == "refresh-slugs":
        try:
            slugs = refresh_slugs_from_archive(config)
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"{len(slugs)} fichas en el archive de la sesión {config.id}")
        if args.write:
            write_slugs(config, slugs)
            print(f"escrito {config.slug_file}")
        else:
            for slug in slugs:
                print(slug)
        return 0

    if args.cmd == "refresh-protocol":
        from pipeline.protocol import layout_from_pdf, parse_protocol_layout, refresh_protocol, write_protocol_index

        try:
            if args.pdf:
                countries = parse_protocol_layout(layout_from_pdf(Path(args.pdf)))
                path = write_protocol_index(countries)
                n = len(countries)
            else:
                path, n = refresh_protocol(user_agent=config.user_agent)
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"{n} países en {path}")
        return 0

    if args.cmd == "roster":
        from pipeline.roster import (
            build_roster,
            export_speaker_names,
            load_roster,
            merge_speakers,
            roster_path,
            write_roster,
        )

        day = args.day
        payload = build_roster(
            config,
            day=day,
            slug=args.slug or None,
            limit=args.limit or None,
        )
        path = roster_path(config, day)
        if args.slug:
            payload = merge_speakers(load_roster(config, day), payload)
        write_roster(payload, path)
        if args.speakers_txt:
            export_path = export_speaker_names(payload, Path(args.speakers_txt))
            print(f"speakers.txt → {export_path}", file=sys.stderr)
        speakers = payload.get("speakers") or []
        errors = 0
        for speaker in speakers:
            slug = speaker.get("slug") or "?"
            if speaker.get("error"):
                errors += 1
                print(f"FAIL {slug} {speaker['error']}", file=sys.stderr)
            else:
                print(
                    f"OK {slug} chosen={speaker.get('chosen') or '—'} "
                    f"{speaker.get('country') or ''}".rstrip(),
                    file=sys.stderr,
                )
        print(
            json.dumps(
                {
                    "speakers": len(speakers),
                    "errors": errors,
                    "path": str(path),
                }
            ),
            file=sys.stderr,
        )
        return 0 if errors == 0 else 2

    if args.cmd == "export-speakers":
        from pipeline.roster import export_speaker_names, load_roster, roster_path

        day = args.day
        payload = load_roster(config, day)
        if not payload:
            print(
                f"error: no hay roster {roster_path(config, day)}",
                file=sys.stderr,
            )
            return 1
        export_path = export_speaker_names(payload, Path(args.speakers_txt))
        names = [
            str(s.get("name") or "").strip()
            for s in (payload.get("speakers") or [])
            if str(s.get("name") or "").strip()
        ]
        print(f"{len(names)} nombres → {export_path}")
        return 0

    if args.cmd == "speakers":
        from pipeline.espeakers import (
            available_days,
            fetch_payload,
            filter_day,
            page_url,
            parse_speakers,
            unique_names,
            write_speakers_txt,
        )

        day = args.day or None
        try:
            payload = fetch_payload(args.url, user_agent=config.user_agent)
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        speakers = parse_speakers(payload)
        chosen = filter_day(speakers, day)
        names = unique_names(chosen)
        days = available_days(speakers)
        if day and not names:
            listed = ", ".join(f"{d} ({n})" for d, n in days) or "ninguno"
            print(
                f"error: no hay oradores con nombre para {day}. "
                f"Días en la lista: {listed}",
                file=sys.stderr,
            )
            return 2
        export_path = write_speakers_txt(
            chosen,
            Path(args.speakers_txt),
            source=page_url(args.url),
            day=day,
        )
        print(f"{len(names)} nombres → {export_path}")
        for meeting_day, count in available_days(chosen):
            print(f"  {meeting_day}: {count}", file=sys.stderr)
        return 0

    if args.cmd == "extract":
        try:
            if args.sources:
                config = _apply_sources(config, args.sources)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        day = args.day
        slug = args.slug or None
        limit = args.limit or None
        try:
            results = fetch_speeches(
                config,
                day=day,
                slug=slug,
                limit=limit,
                dest=dest,
                skip_existing=args.skip_existing,
                require_roster=True,
                reextract=_parse_reextract(args.reextract),
            )
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return _summarize_extract(results)

    if args.cmd == "analyze":
        from pipeline.analyze import analyze_day

        return analyze_day(
            config,
            day=args.day,
            slug=args.slug or None,
            dest=dest,
            prompt_path=Path(args.prompt) if args.prompt else None,
            dry_run=args.dry_run,
        )

    if args.cmd == "coding":
        from pipeline.coding import code_day

        return code_day(
            config,
            day=args.day,
            slug=args.slug or None,
            dest=dest,
            prompt_path=Path(args.prompt) if args.prompt else None,
            dry_run=args.dry_run,
            force=args.force,
        )

    if args.cmd == "coding-sheet":
        from pipeline.coding_sheet import publish_coding_day

        return publish_coding_day(
            config,
            day=args.day,
            dest=dest,
            dry_run=args.dry_run,
        )

    if args.cmd == "alerts":
        keywords = Path(args.keywords) if args.keywords else DEFAULT_KEYWORDS_PATH
        print_alert_report(
            config,
            keywords_path=keywords,
            dest=dest,
            english_only=not args.all_languages,
            as_json=args.json,
        )
        return 0

    if args.cmd == "fetch":
        try:
            if args.sources:
                config = _apply_sources(config, args.sources)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        limit = args.limit or None
        day = args.day or None
        slug = args.slug or None
        planned = select_slugs(config, day=day, slug=slug, limit=limit)
        print(
            f"sesión {config.id} ({config.name}) a extraer: {len(planned)}",
            file=sys.stderr,
        )
        results = fetch_speeches(
            config,
            day=day,
            slug=slug,
            limit=limit,
            dest=dest,
            metadata_only=args.metadata_only,
            skip_existing=args.skip_existing,
            reextract=_parse_reextract(args.reextract),
        )
        return _summarize_extract(results, metadata_only=args.metadata_only)

    if args.cmd == "publish":
        from pipeline.publish import publish_day

        day = args.day or None
        slug = args.slug or None
        if not day and not slug:
            print("error: publish requiere --day o --slug", file=sys.stderr)
            return 1
        return publish_day(
            config,
            day=day,
            slug=slug,
            dest=dest,
            do_github=args.github,
            do_sheet=args.sheet,
            write_csv=not args.no_csv,
        )

    if args.cmd == "progress-site":
        from pipeline.progress import REPO_ROOT, generate_progress_site

        docs = Path(args.docs) if args.docs else (REPO_ROOT / "docs")
        days = [args.day] if args.day else None
        generate_progress_site(
            config,
            docs_dir=docs,
            dest=dest,
            days=days,
        )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
