# Análisis de discursos UNGA

Pipeline batch: baja el roster de oradores, extrae el texto de cada discurso (PDF / audio / video), publica transcripts y (opcional) analiza con Claude hacia Google Sheets.

Esto **no** es el monitor de alertas en vivo. Ver [`README-ALERTAS.md`](README-ALERTAS.md).

```text
roster (HTML) → extract (PDF/OCR/Whisper) → publish (txt + Sheets) → analyze (Claude, opcional)
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

`--skip-existing` no reprocesa lo que ya está. Si un PDF de gadebate falla por WAF, extract sigue con audio (S3) o video (Kaltura).

### 3. Publish

```bash
# Solo Sheets
docker compose -f docker-compose.pipeline.yml run --rm pipeline \
  publish --session 80 --day 2025-09-23 --sheet

# Sheets + commit/push de los .txt
docker compose -f docker-compose.pipeline.yml run --rm pipeline \
  publish --session 80 --day 2025-09-23 --github --sheet
```

### 4. Analyze (opcional)

Requiere prompt real en `pipeline/data/analyze-prompt.md` (hoy es un stub) y `ANTHROPIC_API_KEY`.

```bash
docker compose -f docker-compose.pipeline.yml run --rm pipeline \
  analyze --session 80 --day 2025-09-23
```

Idempotente por `slug|date`. Con stub, sale sin llamar a la API. `--dry-run` lista qué haría. Para forzar prueba con stub: `ANALYZE_ALLOW_STUB=1`.

## Atajo: roster + extract en un comando

`fetch` = scrape + extract. En local, si gadebate responde bien desde tu red:

```bash
docker compose -f docker-compose.pipeline.yml run --rm pipeline \
  fetch --session 80 --day 2025-09-23 --skip-existing
```

Si el scrape de gadebate falla dentro de Docker (WAF/proxy), usá el flujo separado: `roster` con el venv y después `extract` con Compose.

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
  cli.py              comandos (roster, extract, publish, analyze, …)
  roster.py           scrape → JSON
  extract_*.py        PDF / OCR / audio / video
  cascade.py          orden de fuentes
  sheets.py           Google Sheets
  analyze.py          Claude → pestaña Analysis
  data/roster/        JSON diarios
  data/analyze-prompt.md
  out/<session>/<day>/*.txt
```

## Troubleshooting

| Síntoma | Qué probar |
|---|---|
| Fallo scrape gadebate en Docker | Correr `roster` con el venv en el host |
| Exit 137 en extract | OOM: `PIPELINE_WHISPER_MODEL=tiny`, más RAM |
| Whisper se corta al segundo | Mismo: kill por memoria |
| `publish --sheet` falla auth | SA en `PIPELINE_SA_FILE` + Sheet compartido con esa cuenta |
| `analyze` no llama a Claude | Prompt stub en `analyze-prompt.md` o falta `ANTHROPIC_API_KEY` |
