from __future__ import annotations

import csv
import os
import subprocess
import sys
from pathlib import Path

from pipeline.config import PIPELINE_ROOT, SessionConfig
from pipeline.countries import CountryIndex, resolve_countries
from pipeline.env import load_dotenv
from pipeline.journal import load_journal_slugs
from pipeline.metadata import (
    METADATA_COLUMNS,
    MetadataRow,
    build_metadata_row,
    next_speech_id,
    row_values,
)
from pipeline.models import ExtractedSpeech
from pipeline.store import list_speech_txts, parse_speech_txt, speech_relpath


def github_repo_and_branch(root: Path | None = None) -> tuple[str, str]:
    load_dotenv()
    repo = os.environ.get("GITHUB_REPO", "").strip()
    branch = os.environ.get("GITHUB_BRANCH", "").strip()
    cwd = root or PIPELINE_ROOT.parent
    if not repo:
        try:
            url = subprocess.check_output(
                ["git", "remote", "get-url", "origin"],
                cwd=cwd,
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            url = ""
        repo = _parse_github_remote(url) or ""
    if not branch:
        try:
            branch = subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=cwd,
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            branch = "main"
    return repo, branch or "main"


def _parse_github_remote(url: str) -> str:
    raw = url.strip().removesuffix(".git")
    if "github.com:" in raw:
        return raw.split("github.com:", 1)[1]
    if "github.com/" in raw:
        return raw.split("github.com/", 1)[1]
    return ""


def transcript_url_for(relpath: str, *, repo: str = "", branch: str = "") -> str:
    load_dotenv()
    if not repo or not branch:
        detected_repo, detected_branch = github_repo_and_branch()
        repo = repo or detected_repo
        branch = branch or detected_branch
    if not repo:
        return ""
    style = (os.environ.get("GITHUB_TRANSCRIPT_STYLE") or "blob").strip().lower()
    rel = relpath.lstrip("/")
    if style == "raw":
        return f"https://raw.githubusercontent.com/{repo}/{branch}/{rel}"
    return f"https://github.com/{repo}/blob/{branch}/{rel}"


def _git_identity() -> tuple[str, str]:
    name = (
        os.environ.get("GIT_AUTHOR_NAME")
        or os.environ.get("GIT_COMMITTER_NAME")
        or "gwl-pipeline"
    )
    email = (
        os.environ.get("GIT_AUTHOR_EMAIL")
        or os.environ.get("GIT_COMMITTER_EMAIL")
        or "gwl-bot@users.noreply.github.com"
    )
    return name, email


def _github_token() -> str:
    return (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()


def _redact_secret(text: str, secret: str) -> str:
    if not secret:
        return text
    return text.replace(secret, "***")


def _git_push_cmd(cwd: Path) -> list[str]:
    token = _github_token()
    repo = os.environ.get("GITHUB_REPO", "").strip()
    if not repo:
        repo, _ = github_repo_and_branch(cwd)
    if token and repo:
        return [
            "git",
            "push",
            f"https://x-access-token:{token}@github.com/{repo}.git",
            "HEAD",
        ]
    return ["git", "push"]


def publish_github(paths: list[Path], *, message: str, root: Path | None = None) -> bool:
    """git add + commit + push de los .txt del día. True si hubo commit."""
    cwd = root or PIPELINE_ROOT.parent
    rels = []
    for path in paths:
        try:
            rels.append(str(path.resolve().relative_to(cwd.resolve())))
        except ValueError:
            rels.append(str(path))
    if not rels:
        print("github: no hay .txt para versionar", file=sys.stderr)
        return False
    add = subprocess.run(
        ["git", "add", "--", *rels],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if add.returncode != 0:
        print(f"github add falló: {add.stderr.strip() or add.stdout}", file=sys.stderr)
        return False
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", *rels],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if not (status.stdout or "").strip():
        print(
            f"github: {len(rels)} txt ya están en el repo, sin cambios "
            "(no hay commit nuevo; eso es esperado si fetch no reescribió archivos)",
            file=sys.stderr,
        )
        return False
    name, email = _git_identity()
    commit = subprocess.run(
        [
            "git",
            "-c",
            f"user.name={name}",
            "-c",
            f"user.email={email}",
            "commit",
            "-m",
            message,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if commit.returncode != 0:
        print(f"github commit falló: {commit.stderr.strip() or commit.stdout}", file=sys.stderr)
        return False
    push_cmd = _git_push_cmd(cwd)
    push = subprocess.run(push_cmd, cwd=cwd, capture_output=True, text=True)
    if push.returncode != 0:
        err = _redact_secret(push.stderr.strip() or push.stdout, _github_token())
        print(
            f"github push falló (el commit local está hecho): {err}",
            file=sys.stderr,
        )
        return True
    print("github: commit y push OK", file=sys.stderr)
    return True


def write_metadata_csv(path: Path, rows: list[MetadataRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(METADATA_COLUMNS)
        for row in rows:
            writer.writerow(row_values(row))


def _load_speeches(
    config: SessionConfig,
    *,
    day: str | None,
    slug: str | None,
    dest: Path | None,
) -> list[tuple[ExtractedSpeech, Path]]:
    files = list_speech_txts(config, speech_date=day, dest=dest)
    out: list[tuple[ExtractedSpeech, Path]] = []
    for path in files:
        speech = parse_speech_txt(path)
        if slug and speech.slug != slug:
            continue
        if day and speech.speech_date and speech.speech_date != day:
            continue
        out.append((speech, path))
    return out


def _order_speeches(
    config: SessionConfig,
    items: list[tuple[ExtractedSpeech, Path]],
    day: str | None,
) -> list[tuple[ExtractedSpeech, Path]]:
    if not day:
        return sorted(items, key=lambda it: (it[0].speech_date, it[0].slug))
    journal = load_journal_slugs(config, day)
    if not journal:
        print(
            f"journal: no hay {config.journal_dir / (day + '.txt')}; "
            "uso orden alfabético de slug",
            file=sys.stderr,
        )
        return sorted(items, key=lambda it: it[0].slug)
    rank = {slug: i for i, slug in enumerate(journal)}
    return sorted(
        items,
        key=lambda it: (rank.get(it[0].slug, 10_000), it[0].slug),
    )


def _countries_for_publish(config: SessionConfig) -> CountryIndex:
    from pipeline.sheets import can_write_sheets, read_country_records, sheets_settings

    settings = sheets_settings()
    records = None
    if can_write_sheets(settings):
        try:
            records = read_country_records(settings)
        except Exception as exc:  # noqa: BLE001
            print(f"country_list por API falló ({exc}); sigo con CSV", file=sys.stderr)
    return resolve_countries(
        csv_url=settings.country_csv,
        records=records,
        user_agent=config.user_agent,
    )


def build_day_rows(
    config: SessionConfig,
    *,
    day: str | None,
    slug: str | None = None,
    dest: Path | None = None,
    start_id: int = 1,
    repo: str = "",
    branch: str = "",
    countries: CountryIndex | None = None,
) -> list[MetadataRow]:
    items = _order_speeches(
        config, _load_speeches(config, day=day, slug=slug, dest=dest), day
    )
    if not items:
        return []
    countries = countries if countries is not None else _countries_for_publish(config)
    if not repo or not branch:
        detected_repo, detected_branch = github_repo_and_branch()
        repo = repo or detected_repo
        branch = branch or detected_branch
    rows: list[MetadataRow] = []
    next_id = start_id
    for appearance, (speech, path) in enumerate(items, start=1):
        rel = speech_relpath(path, repo_root=PIPELINE_ROOT.parent)
        url = transcript_url_for(rel, repo=repo, branch=branch)
        ficha = config.speaker_url(speech.slug)
        row, warning = build_metadata_row(
            speech,
            countries=countries,
            appearance=appearance,
            transcript_url=url,
            ficha_url=ficha,
        )
        row.id_speech = f"M_{next_id}"
        next_id += 1
        rows.append(row)
        if warning:
            print(warning, file=sys.stderr)
    return rows


def publish_day(
    config: SessionConfig,
    *,
    day: str | None,
    slug: str | None = None,
    dest: Path | None = None,
    do_github: bool = False,
    do_sheet: bool = False,
    write_csv: bool = True,
) -> int:
    load_dotenv()
    items = _load_speeches(config, day=day, slug=slug, dest=dest)
    if not items:
        print("publish: no hay .txt extraídos para esos filtros", file=sys.stderr)
        return 1
    txts = [path for _, path in items]
    github_committed = False
    if do_github:
        label = day or slug or "speeches"
        github_committed = publish_github(
            txts,
            message=f"Add UNGA {config.id} transcripts for {label}",
        )
    start_id = 1
    existing_headers: list[str] | None = None
    existing_records: list[dict] | None = None
    if do_sheet:
        from pipeline.sheets import (
            SheetsError,
            can_write_sheets,
            read_metadata_records,
            sheets_settings,
        )

        settings = sheets_settings()
        if can_write_sheets(settings):
            try:
                existing_headers, existing_records = read_metadata_records(settings)
                start_id = next_speech_id(
                    [str(r.get("id_speech") or "") for r in existing_records]
                )
            except SheetsError as exc:
                print(f"sheet: no pude leer Metadata ({exc})", file=sys.stderr)
                do_sheet = False
        else:
            from pipeline.sheets import sheets_unavailable_reason

            print(
                f"sheet: {sheets_unavailable_reason(settings)}; escribo CSV local.",
                file=sys.stderr,
            )
            do_sheet = False
    rows = build_day_rows(
        config,
        day=day,
        slug=slug,
        dest=dest,
        start_id=start_id,
    )
    if write_csv and rows:
        day_key = day or rows[0].speech_date or "unknown-date"
        csv_dir = (dest or (config.root / "out")) / str(config.id) / day_key
        csv_path = csv_dir / "metadata.csv"
        write_metadata_csv(csv_path, rows)
        print(f"csv: {csv_path}", file=sys.stderr)
    if do_sheet and rows:
        from pipeline.sheets import SheetsError, append_metadata_rows, sheets_settings

        try:
            written = append_metadata_rows(
                rows,
                settings=sheets_settings(),
                existing=existing_records,
                headers=existing_headers,
            )
            print(f"sheet: append {len(written)} filas nuevas", file=sys.stderr)
        except SheetsError as exc:
            print(f"sheet: {exc}", file=sys.stderr)
            return 2
    if do_github:
        github_label = "commit+push" if github_committed else "sin cambios"
    else:
        github_label = "no"
    print(
        f"publish filas={len(rows)} github={github_label} "
        f"sheet={'sí' if do_sheet else 'no'}",
        file=sys.stderr,
    )
    return 0
