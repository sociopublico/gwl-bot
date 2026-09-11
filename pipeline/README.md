Pipeline UNGA (batch): scrape de discursos, traducción a inglés, txt a GitHub y filas a Google Sheets.

No es el monitor de YouTube Live. Esa imagen sigue en `Dockerfile` + `docker-compose.yml`.

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

4. JSON de la service account de Google en `secrets/google-sa.json` (el Sheet tiene que estar compartido con el email de esa cuenta como Editor). Si el archivo está en otra ruta:

   ```
   export GOOGLE_SA_FILE=/ruta/en/el/host/sa.json
   ```

5. Build:

   ```bash
   docker compose -f docker-compose.pipeline.yml build
   ```

## Comandos

Sustituí sesión y fecha. El default del CLI es `80`; para UNGA 81 usá `--session 81`.

```bash
# Discursos del día (Journal → PDF EN → audio EN → PDF original traducido → video)
docker compose -f docker-compose.pipeline.yml run --rm pipeline \
  fetch --session 81 --day 2026-09-22

# Txt a GitHub + filas a la pestaña Metadata
docker compose -f docker-compose.pipeline.yml run --rm pipeline \
  publish --session 81 --day 2026-09-22 --github --sheet

docker compose -f docker-compose.pipeline.yml run --rm pipeline list --session 81
docker compose -f docker-compose.pipeline.yml run --rm pipeline refresh-slugs --session 81 --write
docker compose -f docker-compose.pipeline.yml run --rm pipeline refresh-protocol
```

`--github` commitea solo `pipeline/out/**/*.txt` si cambiaron, y hace push. Hace falta `GITHUB_TOKEN` o un `git push` que ya funcione en ese clone.

## Recursos

PDF EN sin texto extraíble (escaneado o fuentes CID) pasa por OCR (`pdftoppm` + Tesseract) **dentro de la imagen**, antes de Whisper. No hace falta instalar Tesseract en el host. Si cambiás el `Dockerfile`, hay que `build` de nuevo.

- RAM: 2 GB mínimo; **4 GB** si Whisper transcribe audio/video.
- Disco: el volumen `whisper-models` guarda el modelo HF.
- Red: HTTPS a gadebate.un.org, GitHub, Google (Sheets + traducción) y, si hay video, Kaltura.

El repo se monta en `/app`: journals, cache y txt quedan en el host.
