# ─────────────────────────────────────────────────────────────
#  Videl – all-in-one Telegram bot
#
#  UNIVERSAL Dockerfile: the same file lives at ./Dockerfile and
#  ./VidelBot/Dockerfile and works with ANY of these settings:
#    • Dockerfile = Dockerfile           context = repo root
#    • Dockerfile = VidelBot/Dockerfile  context = repo root
#    • Dockerfile = Dockerfile           context = VidelBot/
#  (Railway, Render, Koyeb, Heroku, Northflank, Fly.io, compose …)
# ─────────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    TZ="Asia/Kolkata" \
    PORT=8080

# ffmpeg/mediainfo → encoder, auto-rename, mediainfo · mkvtoolnix/fontconfig → hardsub
# git → /update · 7z/unrar → archive extract · tini → proper PID 1 (clean SIGTERM)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg mediainfo mkvtoolnix fontconfig git wget ca-certificates tzdata tini \
        gcc python3-dev \
        p7zip-full unzip unrar-free \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first → cached layer when only code changes.
# ./requirements.txt and ./VidelBot/requirements.txt are identical copies, so this
# line works whichever folder is the build context.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip && pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Copy the code; if the context is the repo root, take only the VidelBot/ folder.
COPY . /tmp/src
RUN set -eu; \
    if [ -f /tmp/src/VidelBot/run.py ]; then SRC=/tmp/src/VidelBot; else SRC=/tmp/src; fi; \
    cp -a "$SRC"/. /app/; \
    rm -rf /tmp/src; \
    test -f /app/run.py || { echo "run.py not found – check the build context"; exit 1; }; \
    chmod +x /app/extract 2>/dev/null || true; \
    mkdir -p /app/downloads /app/logs

EXPOSE 8080
HEALTHCHECK --interval=2m --timeout=10s --start-period=90s --retries=3 \
  CMD python3 -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/health',timeout=8)" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "run.py"]
