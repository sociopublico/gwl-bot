# gwl-bot

Monitor de un stream de audio/video en vivo (inicialmente YouTube Live). Extrae el audio, lo transcribe con faster-whisper y escribe en stdout cada vez que aparece una keyword.

Este repositorio es un POC: no hay API, base de datos ni frontend. El objetivo es validar captura de audio, latencia, consumo, detección y (opcionalmente) una alerta por email.

```text
STREAM → yt-dlp → FFmpeg (PCM 16 kHz) → faster-whisper → speaker tracker → keyword detector → LOG
                                                                                      ↓
                                                                                email (opcional)
```

## 1. Qué hace el proyecto

Un proceso Python corre dentro de Docker, se conecta a `STREAM_URL`, corta el audio en ventanas de unos segundos, transcribe cada ventana y busca coincidencias exactas de palabras (con word boundaries). Si hay match, loguea y, si SMTP está configurado, manda un email:

```text
2026-09-01 15:32:17 | KEYWORD_DETECTED | women | Lula da Silva | t=3122s | "...talk about women..." | https://www.youtube.com/watch?v=...&t=3122s
```

## 2. Requisitos

Solo en el host:

- Docker
- Docker Compose

No hace falta instalar Python, FFmpeg, Whisper ni yt-dlp en la máquina.

## 3. Configuración

```bash
cp .env.example .env
```

Editá `.env`. Como mínimo:

```env
STREAM_URL=https://www.youtube.com/watch?v=...
KEYWORDS=women,gender,refugees
```

`.env` está en `.gitignore` y no debe subirse al repositorio.

| Variable | Default | Qué hace |
|---|---|---|
| `STREAM_URL` | (obligatorio) | YouTube Live, VOD o URL directa de audio/HLS/RTMP |
| `KEYWORDS` | `women,gender,refugees` | Lista separada por coma. Acepta frases (`human rights`) |
| `STREAM_START_SECONDS` | `0` | Seek en VOD (segundos). En live se ignora. Lula ~47:25 → `2830` |
| `EXIT_ON_EOF` | `true` | Salir al terminar un VOD en vez de rebobinar en loop |
| `WHISPER_MODEL` | `base` | `tiny`, `base`, `small`, `medium`, `base.en`, etc. |
| `LANGUAGE` | `en` | ISO-639-1. Vacío = autodetección |
| `CHUNK_SECONDS` | `20` | Duración de cada ventana de audio |
| `CHUNK_OVERLAP_SECONDS` | `4` | Audio reusado del chunk anterior para no cortar nombres/frases |
| `CONTEXT_WORDS` | `20` | Palabras de contexto a cada lado de la keyword |
| `SPEAKER_TRACKING` | `true` | Rastrea al orador por presentaciones de protocolo |
| `SPEAKER_ALIASES` | vacío | `lula:Luiz Inacio Lula da Silva` para correcciones exactas |
| `SPEAKER_ROSTER` | vacío | Nombres canónicos separados por `;`. Fuzzy match contra ASR |
| `SPEAKER_ROSTER_FILE` | vacío | Archivo con un orador por línea |
| `SPEAKER_ROSTER_THRESHOLD` | `0.62` | Piso de similitud para mapear el nombre de Whisper |
| `SPEAKER_LLM_API_KEY` | vacío | Si está, se usa LLM cuando el regex no extrae un nombre claro |
| `SPEAKER_LLM_BASE_URL` | `https://api.openai.com/v1` | Endpoint OpenAI-compatible |
| `SPEAKER_LLM_MODEL` | `gpt-4o-mini` | Modelo para extraer nombre/cargo/país |
| `SPEAKER_LLM_TIMEOUT` | `8` | Timeout de la llamada LLM |
| `RECONNECT_DELAY` | `10` | Espera entre reintentos si el stream se cae |
| `WHISPER_DEVICE` | `cpu` | Este POC no usa GPU |
| `WHISPER_COMPUTE_TYPE` | `int8` | Cuantización para CPU |
| `WHISPER_BEAM_SIZE` | `1` | `1` es más rápido; `5` un poco más preciso |
| `WHISPER_VAD` | `true` | Filtra silencio antes de transcribir |
| `CPU_THREADS` | `4` | Threads de CTranslate2 / OpenMP |
| `HEARTBEAT_SECONDS` | `60` | Log de “sigo vivo” |
| `AUDIO_READ_TIMEOUT` | `30` | Margen extra esperando cada chunk |
| `COOKIES_FILE` | vacío | Path interno a cookies Netscape, si hace falta |
| `LOG_LEVEL` | `INFO` | `DEBUG` para ver stderr de FFmpeg |
| `SMTP_HOST` | vacío | Si está vacío, no se mandan mails |
| `SMTP_PORT` | `587` | Usar `587` (STARTTLS) o `465` (SSL). No usar `25` en DigitalOcean |
| `SMTP_USER` | vacío | Usuario SMTP; opcional si el server no pide auth |
| `SMTP_PASSWORD` | vacío | App password / secret SMTP |
| `SMTP_FROM` | vacío | Remitente. Obligatorio para activar email |
| `ALERT_EMAIL_TO` | vacío | Destinatarios separados por coma |
| `SMTP_STARTTLS` | `true` | STARTTLS en el puerto 587 |
| `SMTP_SSL` | auto | `true` si el puerto es 465 |
| `ALERT_COOLDOWN_SECONDS` | `120` | Mínimo entre emails de la **misma** keyword |
| `SMTP_TIMEOUT` | `15` | Timeout de conexión SMTP |

`LANGUAGE` vacío activa autodetección. En un stream en un solo idioma conviene fijarlo: Whisper evita un paso extra por chunk y baja latencia/CPU.

## 4. Ejecución local

```bash
cp .env.example .env
# editar STREAM_URL y KEYWORDS
docker compose up --build
```

La primera corrida descarga el modelo de Whisper (aprox. 140 MB para `base`) y lo deja en un volumen Docker. Las siguientes arrancan más rápido.

Para validar el pipeline **sin** YouTube Live, se puede usar un audio directo (FFmpeg lo abre igual):

```env
STREAM_URL=https://raw.githubusercontent.com/openai/whisper/main/tests/jfk.flac
KEYWORDS=country,americans
```

Al terminar el archivo, si `EXIT_ON_EOF=true` (default) el proceso sale. Con `EXIT_ON_EOF=false` espera `RECONNECT_DELAY` y reintenta desde el inicio. En un live real ese loop es el de reconexión.

Para probar un VOD largo sin transcribirlo entero (p.ej. el vivo de 11 h, Lula ~47:25):

```env
STREAM_URL=https://www.youtube.com/watch?v=KnIFmbdRCi0
STREAM_START_SECONDS=2830
EXIT_ON_EOF=true
KEYWORDS=brazil,women,gender
```

## 5. Ver logs

```bash
docker compose logs -f
```

Ejemplo de salida:

```text
2026-09-01 15:30:01 | INFO | Starting stream monitor
2026-09-01 15:30:02 | INFO | Stream connected
2026-09-01 15:30:22 | INFO | Transcribed chunk | audio=20.0s | inference=3.4s | text=Today we want to talk about...
2026-09-01 15:30:22 | SPEAKER_CHANGED | 47:25 | Luiz Inacio Lula da Silva | President of Brazil | source=regex
2026-09-01 15:32:17 | KEYWORD_DETECTED | women | Luiz Inacio Lula da Silva | t=3122s | "...talk about women..." | https://www.youtube.com/watch?v=KnIFmbdRCi0&t=3122s
2026-09-01 15:40:00 | INFO | Still listening | uptime=600s | chunks=28 | detections=3 | speaker=Luiz Inacio Lula da Silva
```

Para parar:

```bash
docker compose down
```

El proceso captura SIGTERM/SIGINT y corta FFmpeg antes de salir.

## 6. Cómo activar alertas por email

El log sigue saliendo siempre. El mail es opcional: si no completás SMTP, el monitor funciona igual que antes.

En `.env`:

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

Reiniciar:

```bash
docker compose up -d
```

Al arrancar deberías ver:

```text
Email alerts enabled | host=smtp.gmail.com:587 | to=alertas@tu-org.org | cooldown=120s
```

Cuando hay detección (y no está en cooldown):

```text
KEYWORD_DETECTED | women | Luiz Inacio Lula da Silva | t=3122s | "...talk about women..." | https://www.youtube.com/watch?v=KnIFmbdRCi0&t=3122s
Email alert sent | to=alertas@tu-org.org | subject=KEYWORD_DETECTED | women | Luiz Inacio Lula da Silva
```

El mail incluye orador (si se pudo inferir), tiempo de video `H:MM:SS`, link con `t=` al segundo de la keyword, un snippet amplio y el transcript del chunk (~20 s).

Varias keywords en el mismo chunk → **un solo email**. Un fallo de SMTP se loguea y el proceso sigue transcribiendo.

### Gmail

1. Activar 2FA en la cuenta.
2. Crear un [App Password](https://myaccount.google.com/apppasswords).
3. Usar ese valor en `SMTP_PASSWORD` (no la clave normal).

### DigitalOcean

Los Droplets **bloquean el puerto 25**. Usá `587` + STARTTLS, o `465` con `SMTP_SSL=true`. SES, Mailgun, Postmark o el SMTP de Google/Microsoft funcionan así.

### Cooldown

Sin cooldown, un live que repite “women” cada 10 segundos llena el inbox. `ALERT_COOLDOWN_SECONDS=120` ignora la misma keyword durante 2 minutos. El log `KEYWORD_DETECTED` **no** se silencia.

## 7. Cómo cambiar keywords

En `.env`:

```env
KEYWORDS=women,gender,refugees,human rights
```

Reiniciar:

```bash
docker compose up -d
```

El matching es case-insensitive y por palabra (`\b`), no por substring:

- `women` matchea `Women`, `WOMEN`
- `women` **no** matchea `superwomen`
- `gender` **no** matchea `transgender`

No hay fuzzy matching ni semántica.

## 8. Orador y link al segundo

En debates tipo ONU alguien presenta al siguiente orador (“His Excellency… Lula da Silva”) y esa persona queda en el podio hasta la próxima presentación.

El tracker:

1. Busca cues de protocolo en el transcript (`his excellency` / `her excellency`, `give the floor`, `the assembly will hear`, …).
2. Si el regex extrae un nombre de ≥2 palabras, actualiza el orador **sin LLM**.
3. Si hay cue pero el nombre sale flojo, y hay `SPEAKER_LLM_API_KEY`, pide a un LLM OpenAI-compatible `{name, title, country}`.
4. Sin API key, sigue solo con regex (el POC no se cae).
5. La presentación se atribuye a la presidencia: el orador nuevo aplica **al chunk siguiente**.
6. Si hay `SPEAKER_ROSTER` o `SPEAKER_ROSTER_FILE`, el nombre que sacó Whisper se compara (fuzzy, sin acentos) contra la lista canónica. `Louis Narsier-Loula Dar Silva` → `Luiz Inacio Lula da Silva`. Los alias exactos (`SPEAKER_ALIASES`) se aplican antes.

Lista corta en env (`;` o saltos de línea):

```env
SPEAKER_ROSTER=Luiz Inacio Lula da Silva;Emmanuel Macron;Donald Trump
```

Lista larga: un nombre por línea en `speakers.txt`, montar el archivo y:

```env
SPEAKER_ROSTER_FILE=/speakers.txt
```

`SPEAKER_ROSTER_THRESHOLD` (default `0.62`) es el piso de similitud. Si dos oradores del roster quedan empatados, no reemplaza (evita confundir dos Silva).

Las keywords del mail/log llevan ese nombre. El `t=` es el segundo de la keyword dentro del video, no el inicio del chunk:

`https://www.youtube.com/watch?v=VIDEO_ID&t=2845s`

En un **VOD** el segundo coincide con el player (más `STREAM_START_SECONDS` si hubo seek). En un **live**, `t=` cuenta desde el inicio del broadcast: se ancla con el `release_timestamp` de yt-dlp. Si YouTube no lo manda, el link es relativo a cuando nos enganchamos y el mail lo aclara. En lives con DVR el `t=` suele permitir rewind; si no, sirve cuando el live pasa a replay (mismo `video_id`).

Tras un reconnect de live se re-sincroniza el reloj; el orador actual se conserva. Si un VOD arranca de cero, el orador se resetea.

## 9. Cómo cambiar el modelo Whisper

```env
WHISPER_MODEL=small
```

Después:

```bash
docker compose up -d --build
```

El modelo se baja solo la primera vez que se usa. El volumen `whisper-models` evita redescargarlo.

Guía rápida:

| Modelo | Accuracy | CPU / RAM | ¿Cuándo? |
|---|---|---|---|
| `tiny` | baja | muy liviano | smoke test |
| `base` | razonable | ~0.5–1 GB | **default del POC** |
| `small` | mejor | ~1–1.5 GB | si `base` pierde keywords |
| `medium`+ | alta | suele no llegar a near-real-time en CPU | no para este POC |

Variantes `.en` (`base.en`, `small.en`) son un poco mejores si el stream es solo inglés.

## 10. Cómo cambiar el chunk size

```env
CHUNK_SECONDS=20
CHUNK_OVERLAP_SECONDS=4
```

Cada ventana dura `CHUNK_SECONDS`. El overlap reusa el final del chunk anterior: con 20 s y 4 s de overlap, Whisper ve 20 s, de los cuales 4 s ya se transcribieron. Eso cubre nombres largos cortados en el borde. Puede repetir una keyword en dos chunks seguidos; el cooldown del mail evita el spam.

El default es **20 s** a propósito: más contexto en la alerta (y en las presentaciones de orador), a costa de más latencia.

Implicancias:

| Chunk | Latencia mínima | CPU | Riesgo |
|---|---|---|---|
| 5–10 s | más baja | más inferencias por minuto | menos contexto; peor para nombres largos |
| 20 s | **default** | menos inferencias, cada una más pesada | mejor contexto; alerta menos inmediata |
| 30 s+ | más alta | aún menos overhead | peor “tiempo real” |

La latencia **no** es `CHUNK_SECONDS`. Es aproximadamente:

```text
atraso HLS de YouTube (5–20 s)
+ espera del chunk (CHUNK_SECONDS)
+ inferencia Whisper (2–12 s en CPU, según modelo)
```

En la práctica, con el default de 20 s una palabra dicha en vivo suele aparecer en el log **25–55 segundos después**. El `t=` del mail apunta al segundo de la keyword, no al inicio de esa ventana. Cada línea `Transcribed chunk` incluye `inference=` para medirlo.

## 11. Limitaciones conocidas

### Latencia

Esto **no** es streaming ASR. Procesamos ventanas cerradas con overlap (`CHUNK_OVERLAP_SECONDS`, default 4 s) para no cortar nombres o keywords en el borde.

### Consumo CPU/RAM

- Imagen Docker + dependencias: del orden de 1–2 GB en disco.
- Runtime con `base` + `int8`: típico **0.5–1.5 GB RAM**.
- CPU: Whisper usa los `CPU_THREADS` al transcribir. En un Droplet de 2 vCPU, `base` suele mantenerse cerca de tiempo real; `small` puede atrasarse si el chunk tarda más que `CHUNK_SECONDS` en transcribirse.
- Primera ejecución: descarga del modelo a Hugging Face.

Sin GPU. `WHISPER_COMPUTE_TYPE=int8` es la configuración correcta para CPU (menos RAM, más rápido que float32).

### YouTube Live

Decisión: **yt-dlp resuelve la URL del live → FFmpeg extrae PCM 16 kHz mono**. No hay scraping manual.

Limitaciones reales:

- YouTube cambia el extractor con frecuencia. Un 403 o “Sign in to confirm you’re not a bot” se arregla en general **actualizando yt-dlp** (`docker compose build --no-cache`) y, a veces, pasando cookies.
- yt-dlp **requiere Deno** dentro del container para resolver challenges JS. Ya está instalado en la imagen.
- Lives con restricción de edad, miembros o región pueden pedir `COOKIES_FILE`.
- Las URLs internas de googlevideo **expiran**. Si FFmpeg se cae, el proceso espera `RECONNECT_DELAY` y vuelve a resolver el stream.
- HLS va varios segundos detrás del “vivo” del player.
- El ToS de YouTube no contempla este tipo de consumo server-side. Usar solo streams para los que tengas derecho. El extractor puede romperse sin aviso.
- Cookies del browser (`--cookies-from-browser`) no aplican dentro de Docker; exportar Netscape a un archivo y montarlo.

### Accuracy de Whisper

- `base` en inglés de estudio funciona bien; acentos, overlap de voces, música de fondo y nombres propios se degradan.
- En silencio, Whisper a veces alucina frases tipo “Thanks for watching”. `WHISPER_VAD=true` mitiga parte de eso.
- Autodetección de idioma en cada chunk suma CPU y puede flippear de idioma.

### Near-real-time vs streaming ASR

Un sistema de streaming ASR (whisper_streaming, WebRTC, etc.) mantiene estado, emite palabras parciales y reduce latencia. Este POC:

1. junta N segundos
2. transcribe el bloque entero
3. busca keywords
4. descarta el audio

Es más simple, predecible y suficiente para validar el pipeline. No reemplaza un ASR streaming de producción.

### Otras

- No se persisten transcripciones completas.
- No hay healthcheck HTTP.
- Si el stream es un VOD y `EXIT_ON_EOF=true`, el proceso termina al EOF. Con `false`, reintenta desde cero.
- El cooldown de email y el orador actual viven en memoria: un restart del container los pierde.
- El tracker de orador no reconoce voces: si no hay presentación (o Whisper la transcribe mal), el hablante queda `unknown` o el anterior.
- En live, si yt-dlp no trae el inicio del broadcast, el `t=` puede no calzar con el player hasta que exista replay.

## 12. Deploy futuro (DigitalOcean Droplet)

El mismo `Dockerfile` + `docker-compose.yml` es el artefacto de deploy. No hay otra forma de instalación.

En el Droplet:

1. Instalar Docker Engine y Docker Compose.
2. Clonar el repo.
3. Copiar `.env` (no commitear secretos).
4. `docker compose up -d --build`.
5. `docker compose logs -f` para observar.

Recomendaciones para el Droplet:

- **RAM:** 2 GB mínimo, **4 GB** más cómodo para `base`/`small`.
- Disco: dejar espacio para la imagen y el volumen del modelo.
- `restart: unless-stopped` ya está en Compose: si el proceso muere, Docker lo levanta. El loop interno ya reintenta si solo se cae el stream.
- Abrir salida HTTPS (YouTube + Hugging Face la primera vez) y **TCP 587 o 465** si usás email. El puerto 25 no sirve en DO.
- No hace falta GPU ni nginx para este POC.
- Para actualizar yt-dlp tras un cambio de YouTube: rebuild de la imagen.

## 13. Pipeline UNGA (otro servidor)

El scrape de discursos **no** usa el compose del monitor. Imagen aparte:

Ver [`pipeline/README.md`](pipeline/README.md).

```bash
docker compose -f docker-compose.pipeline.yml build
docker compose -f docker-compose.pipeline.yml run --rm pipeline fetch --session 81 --day 2026-09-22
docker compose -f docker-compose.pipeline.yml run --rm pipeline publish --session 81 --day 2026-09-22 --github --sheet
```

Cookies opcionales:

```yaml
# en docker-compose.yml, bajo monitor:
# volumes:
#   - ./youtube.cookies.txt:/cookies/youtube.txt:ro
```

```env
COOKIES_FILE=/cookies/youtube.txt
```

## Decisiones técnicas del POC

1. **Obtener audio de YouTube Live:** yt-dlp (Python API, `download=False`) obtiene la URL HLS/DASH + headers. FFmpeg la abre y emite PCM s16le 16 kHz mono por stdout. Es el camino más simple que sigue funcionando en un servidor Linux. No usamos Streamlink ni un downloader propio.
2. **Modelo Whisper:** `base` + `int8` + `beam_size=1` en CPU. `tiny` es demasiado frágil para keywords; `small` es el siguiente paso si el recall no alcanza.
3. **Chunks fijos con overlap:** ventanas de `CHUNK_SECONDS` (default 20) y hop de `CHUNK_SECONDS - CHUNK_OVERLAP_SECONDS`. Un solo hilo lee audio y transcribe.
4. **Idioma:** default `en`. Autodetección solo si `LANGUAGE` queda vacío.
5. **Logs a stdout:** Docker los captura. No hay archivos de log en el container.
6. **Eventos:** `app/events.py` loguea y llama a `app/notifier.py`. Hoy el notifier es SMTP; después se puede sumar Slack sin tocar el detector.

## Estructura

```text
app/
  main.py          loop, señales, reconnect, heartbeat
  config.py        environment variables
  audio.py         yt-dlp + FFmpeg + chunks PCM
  clock.py         reloj de video (origin + PCM fresco)
  youtube.py       video_id y URLs con t=
  transcriber.py   faster-whisper con timestamps de segmento
  transcript.py    segmento ASR (start/end/text)
  speaker.py       presentaciones de protocolo + LLM opcional
  roster.py        fuzzy match de nombres ASR contra lista canónica
  detector.py      regex con word boundaries
  events.py        emisión de detecciones (log + notifier)
  notifier.py      email SMTP + cooldown
  logger.py        formato de logs
```

## Troubleshooting

| Síntoma | Qué probar |
|---|---|
| `STREAM_URL es obligatorio` | `.env` no se está leyendo; confirmar `cp .env.example .env` |
| 403 / bot check de YouTube | rebuild para actualizar yt-dlp; cookies; stream público |
| `timed out waiting for audio` | live caído, URL vencida, o red; el proceso reintenta solo |
| Inferencia > `CHUNK_SECONDS` | bajar a `tiny`/`base`, bajar `CPU_THREADS` no ayuda si ya está saturado; o subir chunk |
| Muchas detecciones falsas por alucinación | `WHISPER_VAD=true`, modelo más grande, revisar silencio |
| `docker compose down` tarda | `stop_grace_period` es 20s; SIGTERM debería ser casi inmediato |
| `Email alerts disabled` | Faltan `SMTP_HOST`, `SMTP_FROM` o `ALERT_EMAIL_TO` |
| `Email alert failed` | Host/puerto/credenciales; en un Droplet no uses puerto 25; Gmail pide app password |
| Mails de más | Subí `ALERT_COOLDOWN_SECONDS` |
