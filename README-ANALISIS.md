# Análisis de discursos UNGA

Pipeline batch: baja el roster de oradores, extrae el texto de cada discurso (PDF / audio / video), publica transcripts y (opcional) analiza con Claude hacia Google Sheets.

Esto **no** es el monitor de alertas en vivo. Ver [`README-ALERTAS.md`](README-ALERTAS.md).

```text
roster (HTML) → extract (PDF/OCR/Whisper) → publish (txt + Sheets) → coding (Claude → CSV) → coding-sheet (Indicators + Emerging_Priorities)
```

Guía asumiendo **todo en local** (laptop/desktop).

## Requisitos (local)

| Pieza | Para qué |
|---|---|
| Python 3.12+ | Comando `roster` (scrape de gadebate) |
| Docker + Compose | `extract`, `publish`, `analyze` (FFmpeg, Tesseract, Whisper van en la imagen) |
| Red a gadebate.un.org | Solo para `roster` |
| ~4 GB RAM libres | Si Whisper tiene que transcribir audio/video |

Opcional según qué pasos corras:

- Service account de Google + Sheet compartido (Editor) → `publish --sheet` / `analyze`
- `GITHUB_TOKEN` → `publish --github`
- `ANTHROPIC_API_KEY` → `analyze`

## Setup una vez

### 1. `.env`

```bash
cp .env.example .env
```

Para el pipeline, lo habitual:

```env
# Sheets (publish --sheet / analyze)
GOOGLE_SHEETS_SPREADSHEET_ID=...
GOOGLE_SHEETS_METADATA_TAB=Metadata
GOOGLE_SHEETS_ANALYSIS_TAB=Analysis
GOOGLE_APPLICATION_CREDENTIALS=/ruta/absoluta/a/tu-sa.json

# Path en el HOST al JSON de la SA (Compose lo monta en /secrets/google-sa.json)
PIPELINE_SA_FILE=./secrets/google-sa.json

# Whisper del pipeline (independiente del bot de alertas)
PIPELINE_WHISPER_MODEL=tiny
PIPELINE_CPU_THREADS=1

# Publish a GitHub (opcional)
# GITHUB_REPO=sociopublico/gwl-bot
# GITHUB_BRANCH=main
# GITHUB_TOKEN=ghp_...

# Analyze con Claude (opcional)
# ANTHROPIC_API_KEY=...
# ANTHROPIC_MODEL=claude-haiku-4-5
# ANTHROPIC_CACHE_TTL=1h   # 1h | 5m | off — prompt caching del system
```

Copiá el JSON de la service account a `secrets/google-sa.json` (o la ruta de `PIPELINE_SA_FILE`) y compartí el Sheet con el email de esa cuenta como Editor.

### 2. Venv para roster

Desde la raíz del repo:

```bash
python3 -m venv pipeline/.venv
pipeline/.venv/bin/pip install -U pip
pipeline/.venv/bin/pip install -r pipeline/requirements.txt
```

### 3. Imagen Docker del pipeline

```bash
docker compose -f docker-compose.pipeline.yml build
```

No hace falta reinstalar nada si solo cambiás `PIPELINE_WHISPER_MODEL`: el modelo se descarga solo la primera vez.

## Flujo local (día a día)

Sustituí `--session` y `--day`. Default de sesión: `80`. Para UNGA 81: `--session 81`.

### 1. Roster

Scrape de fichas HTML en [gadebate.un.org](https://gadebate.un.org). Escribe un JSON; no baja PDF ni corre Whisper.

```bash
pipeline/.venv/bin/python -m pipeline roster --session 80 --day 2025-09-23
```

Para `speakers.txt` del monitor de alertas usá `python -m pipeline speakers` (e-Delegate), no este scrape.

Salida: `pipeline/data/roster/80/2025-09-23.json`.

Cada orador trae `slug`, URLs (`pdf_en`, `audio_en`, `pdf_other`, `video`) y `chosen` (primer origen usable de la cascada).

### 2. Extract

Lee el roster y corre la cascada: PDF EN → audio EN → PDF otro idioma → video.

```bash
docker compose -f docker-compose.pipeline.yml run --rm pipeline \
  extract --session 80 --day 2025-09-23 --skip-existing
```

Ejemplo de log:

```text
OK lithuania source=pdf_en via=ocr chars=9847 elapsed=12.4s
OK brazil source=audio_en via=whisper chars=... elapsed=841s
```

`via` = `pypdf` | `ocr` | `whisper` | `translate`.

Transcripts en `pipeline/out/<session>/<day>/<slug>.txt`.

`--skip-existing` no reprocesa lo que ya está. Si a la tarde aparece un PDF mejor, no lo pisa: re-scrapear el roster y re-extraer el slug:

```bash
pipeline/.venv/bin/python -m pipeline roster --session 80 --day 2025-09-23
docker compose -f docker-compose.pipeline.yml run --rm pipeline \
  extract --session 80 --day 2025-09-23 --skip-existing --reextract brazil
```

En cron: `REEXTRACT=brazil,kenya ./pipeline/scripts/daily.sh`. `write_speech` conserva el `id_speech` / `M_N.txt`.

Si un PDF de gadebate falla por WAF, extract sigue con audio (S3) o video (Kaltura).

### 3. Publish

```bash
# Solo Sheets
docker compose -f docker-compose.pipeline.yml run --rm pipeline \
  publish --session 80 --day 2025-09-23 --sheet

# Sheets + commit/push de los .txt
docker compose -f docker-compose.pipeline.yml run --rm pipeline \
  publish --session 80 --day 2025-09-23 --github --sheet
```

### 4. Coding (Indicators + Emerging_Priorities)

Tras `publish` (hace falta `id_speech` / `M_N.txt`). Usa [`claude-prompt.md`](claude-prompt.md) como methodology.

```bash
# Claude → CSV locales
pipeline/.venv/bin/python -m pipeline coding --session 80 --day 2025-09-23

# CSV → pestañas Indicators + Emerging_Priorities
pipeline/.venv/bin/python -m pipeline coding-sheet --session 80 --day 2025-09-23
```

Idempotente: `coding` saltea `id_speech` ya en `Indicators.csv` (`--force` para recodear). `coding-sheet` saltea por `extract_id` / `id_extract`.

Si Claude omite un indicador o manda un `code` inválido, ese discurso **no** se escribe (no es “No Mention”). Log `FAIL speech=M_N missing=...`. Re-correr `coding` sin `--force` lo recupera. Un `code=0` explícito del modelo sí es No Mention.

`--dry-run` en ambos. Modelo: `ANTHROPIC_MODEL` (recomendado `claude-sonnet-5`).

### 5. Analyze (legado → pestaña Analysis)

Flujo narrativo summary/notes hacia `Analysis` — no es el coding del codebook. Preferí `coding` + `coding-sheet`.

## Atajo: roster + extract en un comando

`fetch` = scrape + extract. En local, si gadebate responde bien desde tu red:

```bash
docker compose -f docker-compose.pipeline.yml run --rm pipeline \
  fetch --session 80 --day 2025-09-23 --skip-existing
```

Si el scrape de gadebate falla dentro de Docker (WAF/proxy), usá el flujo separado: `roster` con el venv y después `extract` con Compose.

## Dashboard de avance (GitHub Pages)

Sitio estático con accordion por día y timeline por orador (fetch orador → discurso → coding). **No se actualiza solo en el browser**: es HTML generado.

- **GitHub Pages:** el workflow [`.github/workflows/pages.yml`](.github/workflows/pages.yml) corre `progress-site` y `alerts-site` para la sesión 81 y la 80 en cada push a `main` que toque roster/coding/out/alerts. La raíz redirige a la 81. No hace falta correr el comando a mano para que se publique; sí hace falta **commit + push de los datos** (roster, CSV, snapshots). En el repo: **Settings → Pages → Source = GitHub Actions**.
- **Local (preview):**

```bash
pipeline/.venv/bin/python -m pipeline progress-site --session 81
pipeline/.venv/bin/python -m pipeline progress-site --session 80
# → docs/index.html (entra a la 81), docs/81/index.html, docs/80/index.html
```

El hito “análisis/coding” se marca OK si hay filas en `Indicators.csv` del día **o** en el snapshot versionado `pipeline/data/coding/<session>/<day>.json` (lo escribe `coding` al terminar).

Para que GitHub Pages cuente bien: commit + push de `docs/` no es obligatorio (Actions regenera el artefacto); sí versioná `pipeline/data/coding/` y/o los CSV `Indicators.csv` / `Emerging_Priorities.csv`.

## Otros comandos

```bash
docker compose -f docker-compose.pipeline.yml run --rm pipeline list --session 81
docker compose -f docker-compose.pipeline.yml run --rm pipeline refresh-slugs --session 81 --write
docker compose -f docker-compose.pipeline.yml run --rm pipeline refresh-protocol
```

## Recursos y tiempos

- OCR (`pdftoppm` + Tesseract) corre **dentro** de la imagen; no hace falta instalarlo en el host.
- Exit **137** ≈ OOM (SIGKILL). Bajá a `PIPELINE_WHISPER_MODEL=tiny` o liberá RAM.
- Un discurso de ~12 MB de audio en CPU tarda varios minutos; el log debería mostrar `whisper 15s`, `whisper 30s`, …
- El repo se monta en `/app`: rosters, cache y `.txt` quedan en el host.

## Estructura relevante

```text
pipeline/
  cli.py              comandos (roster, extract, publish, coding, coding-sheet, …)
  roster.py           scrape → JSON
  extract_*.py        PDF / OCR / audio / video
  cascade.py          orden de fuentes
  sheets.py           Google Sheets (Metadata + Indicators + Emerging_Priorities)
  coding.py           Claude codebook → Indicators.csv + Emerging_Priorities.csv
  coding_sheet.py     CSV → append a Sheets
  claude.py           cliente Anthropic
  analyze.py          legado: Claude → pestaña Analysis
  progress.py         agregación de avance + generador docs/
  alerts_site.py      dashboard de keywords en vivo (docs/alerts.html)
  data/roster/        JSON diarios
  data/alerts/        snapshots de KEYWORD_DETECTED por día (opcional, para Pages)
  data/analyze-prompt.md
  out/<session>/<day>/*.txt (+ Indicators.csv / Emerging_Priorities.csv locales)
docs/                 sitio estático (GitHub Pages)
claude-prompt.md      methodology / codebook para coding
```

## Troubleshooting

| Síntoma | Qué probar |
|---|---|
| Fallo scrape gadebate en Docker | Correr `roster` con el venv en el host |
| Exit 137 en extract | OOM: `PIPELINE_WHISPER_MODEL=tiny`, más RAM |
| Whisper se corta al segundo | Mismo: kill por memoria |
| `publish --sheet` falla auth | SA en `PIPELINE_SA_FILE` + Sheet compartido con esa cuenta |
| `analyze` no llama a Claude | Prompt stub en `analyze-prompt.md` o falta `ANTHROPIC_API_KEY` |

## Sin plan B automático

- **`audio_floor`.** El roster guarda el MP3 de sala (`_FL`) pero la cascada no lo usa (`pdf_en` → `audio_en` → `pdf_other` → `video`). Plan B = `--sources video` o `--reextract` cuando haya `pdf_en` / `audio_en`.
- **OOM cruzado con el monitor de alertas.** No hay lock. Plan B = no correr extract pesado durante el live; acá `PIPELINE_WHISPER_MODEL=tiny`.
- **`--skip-existing` congela un .txt malo** salvo que pases `--reextract slug` (arriba).
- Catch-up del vivo, cookies de YouTube y failover SMTP son del [monitor de alertas](README-ALERTAS.md#sin-plan-b-automático).
