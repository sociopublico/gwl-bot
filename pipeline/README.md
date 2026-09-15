Pipeline UNGA (batch): roster en la laptop, extract/publish en el server, análisis Claude opcional.

No es el monitor de YouTube Live. Esa imagen sigue en `Dockerfile` + `docker-compose.yml`.

El WAF de CloudFront (`x-amzn-waf-action: challenge`) bloquea el scrape de [gadebate.un.org](https://gadebate.un.org) desde el VPS. Las fichas HTML se bajan **en la laptop**. PDFs, audio, video y Whisper siguen en el server.

## En otro servidor

1. Docker Engine + Compose.
2. Clonar el repo.
3. Copiar `.env` (no va a git). Mínimo para Sheets:

   ```
   GOOGLE_SHEETS_SPREADSHEET_ID=...
   GITHUB_REPO=sociopublico/gwl-bot
   GITHUB_BRANCH=main
   GITHUB_TOKEN=ghp_...   # para publish --github (scope repo)
   ```

4. JSON de la service account en `secrets/google-sa.json` (el Sheet compartido con el email de esa cuenta como Editor). En Docker Compose eso se monta en `/secrets/google-sa.json`. Si el archivo está en otra ruta del **server**:

   ```
   export PIPELINE_SA_FILE=/ruta/en/el-server/sa.json
   ```

   No uses un path de tu laptop (`/home/agus/...`) en el `.env` del server.

5. Build:

   ```bash
   docker compose -f docker-compose.pipeline.yml build
   ```

## Flujo diario (cuatro pasos)

### 1. Roster (laptop)

Scrape de fichas HTML. Escribe un JSON versionable, sin bajar PDF ni correr Whisper.

```bash
pipeline/.venv/bin/python -m pipeline roster --session 80 --day 2025-09-23
git add pipeline/data/roster/80/2025-09-23.json && git commit && git push
```

Cada orador trae `slug`, URLs de `pdf_en` / `audio_en` / `pdf_other` / `video`, y `chosen` (primer origen de la cascada que exista).

### 2. Extract (server)

`git pull` y extract **sin** scrape de gadebate. Lee el roster y corre la cascada (PDF → audio → PDF otro idioma → video).

```bash
docker compose -f docker-compose.pipeline.yml run --rm pipeline \
  extract --session 80 --day 2025-09-23 --skip-existing
```

Log por discurso:

```text
OK lithuania source=pdf_en via=ocr chars=9847 elapsed=12.4s
OK brazil source=audio_en via=whisper chars=... elapsed=841s
```

`via` = `pypdf` | `ocr` | `whisper` | `translate`.

Los PDF en `gadebate.un.org/sites/...` suelen devolver HTTP 202 (WAF) desde el VPS. Extract no aborta: marca ese origen como no usable y sigue a `audio_en` (S3) o video (Kaltura).

`fetch` sigue existiendo (scrape + extract). En el server usá `extract`.

### 3. Publish

Sin rediseño: txt a GitHub + filas a la pestaña Metadata.

```bash
docker compose -f docker-compose.pipeline.yml run --rm pipeline \
  publish --session 80 --day 2025-09-23 --github --sheet
```

El cron del server (`pipeline/scripts/daily.sh`) hace `git pull` → `extract --skip-existing` → `publish --github --sheet`.

### 4. Analyze (Claude → pestaña Analysis)

Cuando haya prompt real en `pipeline/data/analyze-prompt.md` y columnas en la pestaña `Analysis`:

```bash
# .env: ANTHROPIC_API_KEY, ANTHROPIC_MODEL=claude-haiku-4-5 (default),
#       GOOGLE_SHEETS_ANALYSIS_TAB=Analysis
docker compose -f docker-compose.pipeline.yml run --rm pipeline \
  analyze --session 80 --day 2025-09-23
```

Idempotente por `slug|date`. Si el prompt sigue siendo un stub, el comando sale sin llamar a la API (`--dry-run` lista qué se haría).

## Comandos

Sustituí sesión y fecha. El default del CLI es `80`; para UNGA 81 usá `--session 81`.

```bash
docker compose -f docker-compose.pipeline.yml run --rm pipeline list --session 81
docker compose -f docker-compose.pipeline.yml run --rm pipeline refresh-slugs --session 81 --write
docker compose -f docker-compose.pipeline.yml run --rm pipeline refresh-protocol
```

`--github` commitea solo `pipeline/out/**/*.txt` si cambiaron, y hace push. Hace falta `GITHUB_TOKEN` o un `git push` que ya funcione en ese clone.

## Recursos

PDF EN sin texto extraíble (escaneado o fuentes CID) pasa por OCR (`pdftoppm` + Tesseract) **dentro de la imagen**, antes de Whisper. No hace falta instalar Tesseract en el host. Si cambiás el `Dockerfile`, hay que `build` de nuevo.

- RAM: 2 GB mínimo; **4 GB libres** si Whisper transcribe audio/video. Exit **137** = SIGKILL (casi siempre OOM), no un restart. El cron entonces arranca **otro** contenedor para publish.
- En el VPS el pipeline usa `PIPELINE_WHISPER_MODEL=tiny` y 1 thread por default (el `.env` del bot YouTube suele traer `WHISPER_MODEL=base` y `CPU_THREADS=4`, demasiado para un droplet chico).
- Disco: el volumen `whisper-models` guarda el modelo HF.
- Red: en el server, HTTPS a S3/Kaltura, GitHub, Google y Anthropic. **No** a gadebate.un.org (eso es el roster en la laptop).
- Un discurso de ~12 MB de audio en CPU tarda varios minutos. El log tiene que mostrar `whisper 15s`, `whisper 30s`, …; si se corta al segundo de `transcribiendo`, fue un kill.

El repo se monta en `/app`: journals, cache, rosters y txt quedan en el host.
