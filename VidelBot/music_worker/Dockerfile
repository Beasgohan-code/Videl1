# Build from the repository root:
#   docker build -f VidelBot/music_worker/Dockerfile -t videl-music-worker .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MALLOC_ARENA_MAX=2 \
    MALLOC_TRIM_THRESHOLD_=65536 \
    MUSIC_CLONE_WORKER=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY VidelBot /app/VidelBot
WORKDIR /app/VidelBot

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r music_worker/requirements.txt

CMD ["python3", "-m", "music_worker"]
