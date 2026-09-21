FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    DENO_INSTALL=/usr/local \
    PATH="/usr/local/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libgomp1 \
        curl \
        ca-certificates \
        unzip \
        xz-utils \
    && rm -rf /var/lib/apt/lists/*

# yt-dlp necesita un runtime JS para resolver los challenges actuales de YouTube.
RUN curl -fsSL https://deno.land/install.sh | sh

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY pipeline/ pipeline/
COPY tests/ tests/
COPY speakers.txt speakers.txt
COPY claude-prompt.md claude-prompt.md

RUN python -c "from app.detector import detect_keywords; \
e=detect_keywords('Today we want to talk about Women and their participation', ('women',), 4); \
assert len(e)==1 and e[0].keyword=='women'; \
assert detect_keywords('the superwomen assembled', ('women',), 2)==[]" \
    && python -m unittest discover -s tests

CMD ["python", "-m", "app"]
