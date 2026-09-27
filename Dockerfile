# ─────────────────────────────────────────────────────────────
#  Videl – all-in-one Telegram bot  ·  root Dockerfile
#  Used by Railway, Render, Koyeb, Heroku (container stack),
#  Northflank, Fly.io, docker compose … – builds ./VidelBot
# ─────────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ="Asia/Kolkata" \
    PORT=8080

# ffmpeg/mediainfo → encoder, auto-rename, mediainfo · git → /update · 7z/unrar → archive extract
# tini → proper PID 1 (clean SIGTERM on redeploys)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg mediainfo mkvtoolnix fontconfig git wget ca-certificates tzdata tini \
        gcc python3-dev \
        p7zip-full unzip unrar-free \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# dependencies first → cached layer when only code changes
COPY VidelBot/requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY VidelBot/ .
RUN chmod +x extract && mkdir -p downloads logs

EXPOSE 8080
HEALTHCHECK --interval=2m --timeout=10s --start-period=90s --retries=3 \
  CMD python3 -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/health',timeout=8)" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "run.py"]
