# gwl-bot

Dos procesos independientes en el mismo repo. Corré cada uno por su cuenta en local.

| Proceso | Qué hace | Guía |
|---|---|---|
| **Alertas** | Monitor de YouTube Live: transcribe, detecta keywords, log/email | [`README-ALERTAS.md`](README-ALERTAS.md) |
| **Análisis** | Pipeline UNGA: roster → extract → publish → analyze | [`README-ANALISIS.md`](README-ANALISIS.md) |

```text
Alertas:   STREAM → Whisper → keywords → LOG / email
Análisis:  roster → PDF/audio/video → .txt → Sheets / Claude
```

## Arranque rápido

### Alertas

```bash
cp .env.example .env   # STREAM_URL + KEYWORDS
docker compose up --build
```

### Análisis

```bash
cp .env.example .env
python3 -m venv pipeline/.venv
pipeline/.venv/bin/pip install -r pipeline/requirements.txt
docker compose -f docker-compose.pipeline.yml build

pipeline/.venv/bin/python -m pipeline roster --session 80 --day 2025-09-23
docker compose -f docker-compose.pipeline.yml run --rm pipeline \
  extract --session 80 --day 2025-09-23 --skip-existing
```

Detalles, variables y troubleshooting: en cada README de arriba.
