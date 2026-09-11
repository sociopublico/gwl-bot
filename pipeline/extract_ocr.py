from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from pipeline.cascade import SourceUnavailable
from pipeline.extract_pdf import clean_pdf_text

_PAGE_NUM = re.compile(r"(\d+)$")

# ISO 639-1 (fichas) → códigos de Tesseract.
_TESS_LANG = {
    "en": "eng",
    "es": "spa",
    "fr": "fra",
    "pt": "por",
    "ar": "ara",
    "de": "deu",
    "it": "ita",
    "nl": "nld",
    "ru": "rus",
    "pl": "pol",
    "tr": "tur",
    "uk": "ukr",
    "ro": "ron",
    "cs": "ces",
    "sk": "slk",
    "hu": "hun",
    "el": "ell",
    "sv": "swe",
    "fi": "fin",
    "da": "dan",
    "no": "nor",
    "nb": "nor",
    "id": "ind",
    "vi": "vie",
    "th": "tha",
    "ko": "kor",
    "ja": "jpn",
    "zh": "chi_sim",
    "he": "heb",
    "fa": "fas",
    "hi": "hin",
    "bn": "ben",
    "ur": "urd",
}


def tesseract_lang_for(iso: str) -> str:
    code = (iso or "").strip().lower()
    if code in {"", "und", "unknown"}:
        return "eng"
    return _TESS_LANG.get(code, "eng")


def _bundled_tesseract_prefix() -> Path | None:
    prefix = Path(__file__).resolve().parent / "tools" / "tesseract"
    if (prefix / "usr" / "bin" / "tesseract").is_file():
        return prefix
    return None


def _tesseract_command() -> tuple[str, dict[str, str]] | None:
    env = os.environ.copy()
    found = shutil.which("tesseract")
    if found:
        return found, env
    prefix = _bundled_tesseract_prefix()
    if prefix is None:
        return None
    lib = prefix / "usr" / "lib" / "x86_64-linux-gnu"
    tessdata = prefix / "usr" / "share" / "tesseract-ocr" / "5" / "tessdata"
    env["LD_LIBRARY_PATH"] = f"{lib}{os.pathsep}{env.get('LD_LIBRARY_PATH', '')}"
    if tessdata.is_dir():
        env["TESSDATA_PREFIX"] = str(tessdata)
    return str(prefix / "usr" / "bin" / "tesseract"), env


def ocr_tools_available() -> bool:
    return shutil.which("pdftoppm") is not None and _tesseract_command() is not None


def ocr_pdf_text(
    blob: bytes,
    *,
    lang: str = "eng",
    dpi: int = 200,
    cache_path: Path | None = None,
    label: str = "pdf",
) -> str:
    """Renderiza el PDF y corre Tesseract. Usar cuando pypdf no saca texto usable."""
    if cache_path and cache_path.is_file() and cache_path.stat().st_size > 0:
        cached = cache_path.read_text(encoding="utf-8").strip()
        if cached:
            print(f"  ocr cache {cache_path.name}", file=sys.stderr, flush=True)
            return cached
    if not blob:
        raise SourceUnavailable("ocr: PDF vacío")
    tess = _tesseract_command()
    if shutil.which("pdftoppm") is None or tess is None:
        raise SourceUnavailable(
            "ocr: falta pdftoppm o tesseract-ocr (apt install tesseract-ocr poppler-utils)"
        )
    tess_bin, tess_env = tess
    tess_lang = lang.strip() or "eng"
    with tempfile.TemporaryDirectory(prefix="gwl-ocr-") as tmp:
        work = Path(tmp)
        pdf_path = work / "in.pdf"
        pdf_path.write_bytes(blob)
        prefix = work / "page"
        try:
            subprocess.run(
                ["pdftoppm", "-png", "-r", str(dpi), str(pdf_path), str(prefix)],
                check=True,
                timeout=180,
                capture_output=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise SourceUnavailable(f"ocr: pdftoppm timeout ({label})") from exc
        except (OSError, subprocess.CalledProcessError) as exc:
            raise SourceUnavailable(f"ocr: pdftoppm falló ({label})") from exc
        pages = sorted(
            work.glob("page*.png"),
            key=lambda p: int(_PAGE_NUM.search(p.stem).group(1)) if _PAGE_NUM.search(p.stem) else 0,
        )
        if not pages:
            raise SourceUnavailable(f"ocr: pdftoppm no generó páginas ({label})")
        print(
            f"ocr {label} pages={len(pages)} lang={tess_lang} dpi={dpi}",
            file=sys.stderr,
            flush=True,
        )
        parts: list[str] = []
        for idx, image in enumerate(pages, 1):
            print(f"  ocr {idx}/{len(pages)}", file=sys.stderr, flush=True)
            try:
                proc = subprocess.run(
                    [
                        tess_bin,
                        str(image),
                        "stdout",
                        "-l",
                        tess_lang,
                        "--psm",
                        "4",
                    ],
                    env=tess_env,
                    check=True,
                    timeout=90,
                    capture_output=True,
                    text=True,
                )
            except subprocess.TimeoutExpired as exc:
                raise SourceUnavailable(f"ocr: tesseract timeout página {idx} ({label})") from exc
            except (OSError, subprocess.CalledProcessError) as exc:
                hint = ""
                if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
                    hint = f": {exc.stderr.strip().splitlines()[-1][:200]}"
                raise SourceUnavailable(
                    f"ocr: tesseract falló página {idx} ({label}){hint}"
                ) from exc
            parts.append(proc.stdout or "")
    text = clean_pdf_text("\n".join(parts))
    if cache_path and text:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(text + "\n", encoding="utf-8")
    return text
