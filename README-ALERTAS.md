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
| `WEBTV_URL` | vacío | Página del meeting en UN Web TV. Solo se usa para el reloj del vivo (no va en el mail). El canal 24/7 no tiene DVR usable |
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
| `SPEAKER_ROSTER_DIR` | `/roster` en Compose | Carpeta con `{día}.json` del pipeline. Si está el del día (hora NY), manda sobre `SPEAKER_ROSTER_FILE`. Se relee sola cuando cambia; si falta, log + mail `ROSTER_STALE` |
| `LOG_LEVEL` | `INFO` | `DEBUG` para ver stderr de FFmpeg |
| `LOG_DIR` | `logs` | Directorio de `full.log` + `highlights.log` (flush inmediato) |
| `WATCHDOG_SECONDS` | `180` | Mail `MONITOR_STALE` + HEALTHCHECK si no hay chunk transcrito. `0` = apagado |
| `WATCHDOG_EMAIL_COOLDOWN` | `600` | Mínimo entre mails de “estoy ciego” |

SMTP (opcional; sin esto solo hay log):

| Variable | Qué hace |
|---|---|
| `SMTP_HOST` | Si está vacío, no se mandan mails |
| `SMTP_PORT` | Mailgun en DO: `2525` (STARTTLS). `587`/`465` suelen estar bloqueados |
| `SMTP_USER` / `SMTP_PASSWORD` | Credenciales (slot A; horas pares si hay rotación) |
| `SMTP_PASSWORD_B` | Segunda key (opcional). Horas impares usan slot B |
| `SMTP_USER_B` / `SMTP_FROM_B` | Opcional; si vacío, reusan A |
| `SMTP_FROM` | Remitente (obligatorio para activar email) |
| `ALERT_EMAIL_TO` | Destinatarios separados por coma |
| `ALERT_COOLDOWN_SECONDS` | Mínimo entre emails de la **misma** keyword (default `120`) |
| `ALERT_TEXT_BEFORE_SECONDS` | Segundos de transcripción anteriores a la keyword en el mail inmediato (default `75`) |
| `ALERT_TEXT_AFTER_SECONDS` | Segundos posteriores que el mail inmediato espera antes de enviarse (default `30`) |
| `ALERT_BATCH_PER_SPEAKER` | `false` (default): mail en el momento. `true`: un mail por orador cuando cambia el siguiente |

## Correr local

```bash
docker compose up --build
```

La primera vez descarga el modelo de Whisper (~140 MB para `base`) al volumen `whisper-models`.

### Logs en disco

Cada línea se escribe y flushea al toque (no hace falta esperar al fin del proceso):

| Archivo | Contenido |
|---|---|
| `logs/full.log` | Igual que la consola |
| `logs/highlights.log` | Solo `SPEAKER_CHANGED`, `KEYWORD_DETECTED`, email sent/failed/cooldown, `MONITOR_STALE`, errores, inicio/fin de sesión |
| `logs/keywords.jsonl` | Una línea JSON por `KEYWORD_DETECTED` (para el dashboard) |
| `logs/speeches.json` | Citas todavía no mandadas, si `ALERT_BATCH_PER_SPEAKER=true` |

Rotación diaria (14 días). Compose monta `./logs` → `/app/logs`.

Consola:

```bash
docker compose logs -f
# o
tail -f logs/full.log
tail -f logs/highlights.log
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

El mailer es **SMTP**. El puerto lo publica el servidor, no el cliente: DigitalOcean bloquea salida 25/465/587; Mailgun también escucha **2525** (STARTTLS).

### Mailgun SMTP

1. Dominio verificado en Mailgun (SPF + DKIM).
2. Sending → Domain settings → SMTP credentials (`postmaster@tu-dominio` + SMTP password, no la API key).
3. En `.env`:

```env
SMTP_HOST=smtp.mailgun.org
SMTP_PORT=2525
SMTP_USER=postmaster@mails.tudominio.com
SMTP_PASSWORD=xxxxxxxx
SMTP_FROM=bot@mails.tudominio.com
ALERT_EMAIL_TO=vos@org.org,companera@org.org
SMTP_STARTTLS=true
ALERT_COOLDOWN_SECONDS=120
```

EU: `SMTP_HOST=smtp.eu.mailgun.org`. Puertos Mailgun: `25`, `465` (TLS), `587` y `2525` (STARTTLS). En DO usá `2525`.

```bash
docker compose exec monitor python -m app.mail_test --probe
docker compose exec monitor python -m app.mail_test --to vos@org.org
# sin tumbar el live:
docker compose run --rm --no-deps --build monitor python -m app.mail_test --probe
```

Si cambiaste `.env`, `docker compose up -d` para que el contenedor tome las variables.

### Gmail u otro SMTP

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

### Un mail por discurso

Por defecto cada keyword sale en el momento (`Alert delivery | immediate`), con el pasaje de antes y después.

`ALERT_BATCH_PER_SPEAKER=true` espera al discurso entero y manda **un solo mail por orador** cuando el chair presenta al siguiente. Cada cita se guarda en `logs/speeches.json`. Cortar el proceso, o que se caiga el stream, no manda el mail: las citas quedan en ese archivo.

Para mandarlo a mano, por nombre o por el slug del país:

```bash
docker compose exec monitor python -m app.speech_mail --list
docker compose exec monitor python -m app.speech_mail --country libya
docker compose exec monitor python -m app.speech_mail --name "Mohamed Younis Menfi"
```

El slug sale de bajar a minúsculas y sacar el paréntesis: `Libya` → `libya`, `Iran (Islamic Republic of)` → `iran`, `Syrian Arab Republic` → `syrian-arab-republic`. `--list` lo muestra. Un nombre parcial alcanza si hay una sola persona (`Menfi`).

Asunto: `gender, women | Mohamed Younis Menfi | Libya`. Adentro, cada cita con su keyword y su hora (UTC, Nueva York, Madrid), en orden de tiempo. No agrupa por keyword. Cada cita incluye el mismo pasaje que el mail inmediato: `ALERT_TEXT_BEFORE_SECONDS` antes de la keyword (default 75 s) y `ALERT_TEXT_AFTER_SECONDS` después (default 30 s). El cooldown por keyword no aplica a este mail.

Con Gmail: activar 2FA y usar un [App Password](https://myaccount.google.com/apppasswords).

## Oradores (speaker tracking)

El monitor detecta cambios de orador desde el ASR cuando el chair presenta (`His Excellency…`, `give the floor`, etc.). En el chunk de la intro las keywords quedan como `unknown`; el nombre nuevo aplica en el chunk siguiente (cuando arranca a hablar la persona). El orador actual queda en `logs/speaker.json`. Un `docker compose up --build` lo retoma, salvo que haya empezado el día anterior (hora de Nueva York) o hace más de 90 minutos.

Eso **no** es lo mismo que el pipeline de análisis: ahí Whisper solo transcribe un discurso ya aislado y el nombre viene del roster gadebate.

Roster del día: Compose monta `pipeline/data/roster/81` en `/roster`. Si existe `/roster/{día}.json` (día de Nueva York), el monitor usa ese roster y lo relee solo cuando cambia (se chequea cada ~60 s). Alcanza con correr `python -m pipeline roster --day ...`, subir el JSON y hacer `git pull` en el servidor, sin reiniciar. Si falta el JSON del día, usa `speakers.txt` y avisa con `ROSTER_STALE` (log y mail).

Si el chair presenta con nombre completo y país a alguien que no está en el roster, el monitor lo acepta igual (`SPEAKER_UNVERIFIED`, `source=asr-unverified`) para cerrar el discurso anterior y no mezclar citas. Un nombre suelto sin país sigue ignorándose.

Para mejorar el fuzzy match del monitor, bajá nombres, país y cargo desde e-speakers:

```bash
pipeline/.venv/bin/python -m pipeline speakers \
  --url https://e-speakers.e-delegate.un.org/6aa9a60e2a8f905c7035541515092026 \
  --day 2026-09-22 \
  --speakers-txt speakers.txt
```

El archivo queda `nombre | país | cargo`. El monitor matchea el nombre **y**, si el chair dice *“President of Brasil”* o *“prime minister of Canada”* sin un nombre usable, usa país/cargo.

Sin `--day` escribe todos los nombres que ya figuran en la lista. Si el día está todo en *Forthcoming*, no pisa `speakers.txt`.

Después de escribir el archivo, reiniciá el monitor para que lo recargue:

```bash
docker compose up -d --force-recreate
```

El roster de análisis (`python -m pipeline roster`) sigue siendo otra cosa: fichas de gadebate con PDF/audio/video. Si ese JSON ya existe, todavía se puede exportar nombres con `export-speakers`.

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
KEYWORD_DETECTED | women | Luiz Inacio Lula da Silva | t=3122s | "...talk about women..."
```

El mail espera `ALERT_TEXT_AFTER_SECONDS` (default 30 s) y manda el texto desde `ALERT_TEXT_BEFORE_SECONDS` (default 75 s) antes de la keyword hasta ese después, con la keyword en negrita (HTML). También lleva hora UTC, Nueva York y Madrid, orador y el link de YouTube sin timestamp (en vivo ignora `t=`). `EMAIL_WAITING_CONTEXT` es el mail demorado hasta juntar el texto de después. Si `WEBTV_URL` está puesto, solo ajusta el reloj del vivo; no se manda en el mail. En el log, `origin=… reliable=false` y `t=12s?` quieren decir que ese reloj no es seekable.

El monitor también appendea `logs/keywords.jsonl` (una línea por hit; no entra SPEAKER_CHANGED ni watchdog).

## Dashboard de keywords

Misma idea que el [dashboard de análisis](README-ANALISIS.md#dashboard-de-avance-github-pages): HTML estático, accordion por día → orador → hits con contexto. No muestra healthcheck ni cambios de orador.

No se refresca solo en el browser. En el VPS el bot escribe el jsonl; para verlo / publicarlo:

```bash
# En el server, con los logs montados
pipeline/.venv/bin/python -m pipeline alerts-site --session 81 --logs logs --snapshot
# → docs/81/alerts.html + docs/81/data/alerts.json
# --snapshot copia a pipeline/data/alerts/81/<día>.json para versionar
```

Si un hit quedó con el orador equivocado (el tracker no vio el cambio), `app.keyword_fix` lo corrige por ventana UTC en `keywords.jsonl`, `highlights.log*` y los snapshots a la vez (el dashboard deduplica por orador). Sin `--apply` solo muestra los cambios:

```bash
docker compose exec monitor python -m app.keyword_fix \
  --fix "2026-09-25T14:30:00Z..2026-09-25T14:47:20Z=Christophe Mirmand|Monaco"
# snapshots: correrlo en el host con --log-dir logs --snapshots pipeline/data/alerts/81
```

Commit + push de `pipeline/data/alerts/` dispara GitHub Pages (el workflow arma la 81 y la 80). Local: `docs/index.html` entra a la 81; la 80 está en `docs/80/`.

Latencia típica con chunks de 20 s: **25–55 s** después de que se dijo la palabra (HLS de YouTube + chunk + inferencia).

## Troubleshooting

| Síntoma | Qué probar |
|---|---|
| `STREAM_URL es obligatorio` | Falta `.env` o no se está leyendo |
| 403 / bot check de YouTube | `docker compose build --no-cache`; cookies Netscape montadas |
| `timed out waiting for audio` | Live caído, URL vencida o red; reintenta solo |
| Inferencia > `CHUNK_SECONDS` | Bajar a `tiny`/`base` o subir `CHUNK_SECONDS` |
| `Email alerts disabled` | Faltan `SMTP_HOST`, `SMTP_FROM` o `ALERT_EMAIL_TO` |
| `MONITOR_STALE` / `unhealthy` | No transcribe: live caído, 403, o Whisper colgado. El contenedor **no** se reinicia solo; `docker compose ps` + el mail. `WATCHDOG_SECONDS=0` lo apaga |
| Mails de más | Subí `ALERT_COOLDOWN_SECONDS` |

## Cookies de YouTube (si hace falta)

Si yt-dlp falla con `Sign in to confirm you're not a bot` (típico en VPS/datacenter):

1. En tu **laptop** (logueada en YouTube), exportá cookies Netscape. Lo más simple: extensión [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) en `youtube.com` → guardá como `youtube.cookies.txt`.
2. Copiá el archivo a la raíz del repo **en el server** (`~/traefik/gwl-bot/youtube.cookies.txt`).
3. En `.env`:

```env
COOKIES_FILE=/cookies/youtube.txt
```

4. Compose monta `./youtube.cookies.txt:/cookies/youtube.txt` (escribible: yt-dlp actualiza el archivo). Reiniciá:

```bash
# si el archivo aún no existe, creá uno vacío solo para que el mount no falle, después reemplazalo
touch youtube.cookies.txt
docker compose up -d
```

Las cookies vencen; si vuelve el error de bot, re-exportá. **No subas** `youtube.cookies.txt` a git.

Alternativa de smoke test **sin** YouTube: URL directa de audio (p.ej. un `.flac`/HLS) en `STREAM_URL`.

## Sin plan B automático

Estos huecos **no** se recuperan solos. El watchdog avisa si el monitor está ciego; no rellena el audio perdido.

- **Audio perdido al reconectar.** `live_from_start=False`: al volver se engancha al vivo actual. Plan B = `highlights.log` + pipeline del discurso publicado.
- **YouTube 403 / bot-check.** El loop reconecta como si fuera un corte de red. Plan B = montar `COOKIES_FILE` (arriba) y mirar `MONITOR_STALE` si queda ciego.
- **SMTP A/B.** La rotación es por hora par/impar, no failover. Si A falla, no se prueba B. Plan B = logs. El mail de watchdog usa el mismo SMTP (si A está muerto a esa hora, el HEALTHCHECK y `highlights.log` siguen).
- **OOM cruzado con extract.** No hay lock entre el monitor (`WHISPER_MODEL=base`) y el pipeline. Plan B = no correr extract pesado durante el live; el compose del pipeline ya fuerza `tiny`.

## Código

```text
app/
  main.py          loop, señales, reconnect, heartbeat, watchdog
  watchdog.py      .heartbeat + MONITOR_STALE
  healthcheck.py   Docker HEALTHCHECK
  audio.py         yt-dlp + FFmpeg + chunks PCM
  transcriber.py   faster-whisper
  speaker.py       presentaciones de protocolo + LLM opcional
  detector.py      regex con word boundaries
  events.py        log + dedup + notifier
  notifier.py      email SMTP + cooldown + rotación de keys
  logger.py        consola + full.log + highlights.log
```
