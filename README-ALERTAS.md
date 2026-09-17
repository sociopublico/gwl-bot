# Alertas en vivo (YouTube)

Monitor de un stream de audio/video. Extrae el audio, lo transcribe con faster-whisper y escribe en stdout cada vez que aparece una keyword. Opcionalmente manda email.

```text
STREAM → yt-dlp → FFmpeg (PCM 16 kHz) → faster-whisper → speaker tracker → keyword detector → LOG
                                                                                      ↓
                                                                                email (opcional)
```

Este proceso **no** es el pipeline de discursos UNGA. Ver [`README-ANALISIS.md`](README-ANALISIS.md).

## Requisitos (local)

- Docker
- Docker Compose

No hace falta instalar Python, FFmpeg, Whisper ni yt-dlp en la máquina.

## Setup

```bash
cp .env.example .env
```

Editá `.env`. Mínimo:

```env
STREAM_URL=https://www.youtube.com/watch?v=...
KEYWORDS=women,gender,refugees
```

`.env` no se sube a git.

### Variables útiles

| Variable | Default | Qué hace |
|---|---|---|
| `STREAM_URL` | (obligatorio) | YouTube Live, VOD o URL directa de audio/HLS/RTMP |
| `KEYWORDS` | `women,gender,refugees` | Lista separada por coma. Acepta frases (`human rights`) |
| `STREAM_START_SECONDS` | `0` | Seek en VOD (segundos). En live se ignora |
| `EXIT_ON_EOF` | `true` | Salir al terminar un VOD |
| `WHISPER_MODEL` | `base` | `tiny`, `base`, `small`, `medium`, `base.en`, etc. |
| `LANGUAGE` | `en` | ISO-639-1. Vacío = autodetección |
| `CHUNK_SECONDS` | `20` | Duración de cada ventana de audio |
| `CHUNK_OVERLAP_SECONDS` | `4` | Overlap para no cortar nombres/frases |
| `CPU_THREADS` | `4` | Threads de Whisper en CPU |
| `SPEAKER_TRACKING` | `true` | Rastrea orador por presentaciones de protocolo |
| `SPEAKER_ROSTER_FILE` | vacío | Archivo con un orador por línea (Compose monta `speakers.txt`) |
| `LOG_LEVEL` | `INFO` | `DEBUG` para ver stderr de FFmpeg |

SMTP (opcional; sin esto solo hay log):

| Variable | Qué hace |
|---|---|
| `SMTP_HOST` | Si está vacío, no se mandan mails |
| `SMTP_PORT` | `587` (STARTTLS) o `465` (SSL) |
| `SMTP_USER` / `SMTP_PASSWORD` | Credenciales |
| `SMTP_FROM` | Remitente (obligatorio para activar email) |
| `ALERT_EMAIL_TO` | Destinatarios separados por coma |
| `ALERT_COOLDOWN_SECONDS` | Mínimo entre emails de la **misma** keyword (default `120`) |

## Correr local

```bash
docker compose up --build
```

La primera vez descarga el modelo de Whisper (~140 MB para `base`) al volumen `whisper-models`.

Logs:

```bash
docker compose logs -f
```

Parar:

```bash
docker compose down
```

### Smoke test sin YouTube Live

```env
STREAM_URL=https://raw.githubusercontent.com/openai/whisper/main/tests/jfk.flac
KEYWORDS=country,americans
```

### Probar un VOD desde un minuto concreto

```env
STREAM_URL=https://www.youtube.com/watch?v=KnIFmbdRCi0
STREAM_START_SECONDS=2830
EXIT_ON_EOF=true
KEYWORDS=brazil,women,gender
```

## Keywords

```env
KEYWORDS=women,gender,refugees,human rights
```

Reiniciar con `docker compose up -d`.

Matching case-insensitive y por palabra (`\b`):

- `women` matchea `Women`
- `women` **no** matchea `superwomen`
- `gender` **no** matchea `transgender`

## Email (opcional)

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=tu-cuenta@gmail.com
SMTP_PASSWORD=xxxx xxxx xxxx xxxx
SMTP_FROM=tu-cuenta@gmail.com
ALERT_EMAIL_TO=alertas@tu-org.org
SMTP_STARTTLS=true
ALERT_COOLDOWN_SECONDS=120
```

```bash
docker compose up -d
```

Al arrancar deberías ver `Email alerts enabled | ...`.

Con Gmail: activar 2FA y usar un [App Password](https://myaccount.google.com/apppasswords).

## Cambiar el modelo Whisper

```env
WHISPER_MODEL=small
```

```bash
docker compose up -d
```

No hace falta `pip install`. El modelo nuevo se descarga solo la primera vez que se usa.

| Modelo | Accuracy | CPU / RAM | ¿Cuándo? |
|---|---|---|---|
| `tiny` | baja | muy liviano | smoke test |
| `base` | razonable | ~0.5–1 GB | **default** |
| `small` | mejor | ~1–1.5 GB | si `base` pierde keywords |
| `medium`+ | alta | suele no llegar a near-real-time en CPU | no recomendado acá |

## Qué ver en el log

```text
KEYWORD_DETECTED | women | Luiz Inacio Lula da Silva | t=3122s | "...talk about women..." | https://www.youtube.com/watch?v=...&t=3122s
```

Latencia típica con chunks de 20 s: **25–55 s** después de que se dijo la palabra (HLS de YouTube + chunk + inferencia).

## Troubleshooting

| Síntoma | Qué probar |
|---|---|
| `STREAM_URL es obligatorio` | Falta `.env` o no se está leyendo |
| 403 / bot check de YouTube | `docker compose build --no-cache`; cookies Netscape montadas |
| `timed out waiting for audio` | Live caído, URL vencida o red; reintenta solo |
| Inferencia > `CHUNK_SECONDS` | Bajar a `tiny`/`base` o subir `CHUNK_SECONDS` |
| `Email alerts disabled` | Faltan `SMTP_HOST`, `SMTP_FROM` o `ALERT_EMAIL_TO` |
| Mails de más | Subí `ALERT_COOLDOWN_SECONDS` |

## Cookies de YouTube (si hace falta)

En `docker-compose.yml`, bajo `monitor`:

```yaml
volumes:
  - ./youtube.cookies.txt:/cookies/youtube.txt:ro
```

```env
COOKIES_FILE=/cookies/youtube.txt
```

## Código

```text
app/
  main.py          loop, señales, reconnect, heartbeat
  audio.py         yt-dlp + FFmpeg + chunks PCM
  transcriber.py   faster-whisper
  speaker.py       presentaciones de protocolo + LLM opcional
  detector.py      regex con word boundaries
  events.py        log + notifier
  notifier.py      email SMTP + cooldown
```
