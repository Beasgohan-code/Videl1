# ─────────────────────────────────────────────────────────────
#  Videl – all-in-one Telegram utility & group-management bot
#
#  Root Dockerfile (outside VidelBot/):
#    • docker build -t videl .                 (repo root context — default)
#    • docker compose up -d --build
#    • Railway / Render / Koyeb / Heroku / Fly.io / Northflank
#
#  Also supports `-f ../Dockerfile VidelBot` if invoked with VidelBot/ context.
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

# Dependencies first → cached layer when only application code changes.
# Install requirements, then remove richgram's transitive `kurigram` package and
# force-reinstall `wzgram==3.1.6` so `pyrogram` resolves to WzGram Layer 232.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip \
    && pip install -r /tmp/requirements.txt \
    && (pip uninstall -y kurigram || true) \
    && pip install --no-deps --force-reinstall wzgram==3.1.6 \
    && python3 -c "import pyrogram, richgram, aiogram; assert pyrogram.__version__ == '3.1.6' and richgram.RICH_AVAILABLE" \
    && rm /tmp/requirements.txt

# Copy VidelBot/ from the repo root context into /app (falls back cleanly if context is VidelBot/).
COPY . /tmp/src
RUN set -eu; \
    if [ -f /tmp/src/VidelBot/run.py ]; then SRC=/tmp/src/VidelBot; else SRC=/tmp/src; fi; \
    cp -a "$SRC"/. /app/; \
    rm -rf /tmp/src; \
    test -f /app/run.py || { echo "run.py not found – check the build context"; exit 1; }; \
    chmod +x /app/extract 2>/dev/null || true; \
    mkdir -p /app/downloads /app/logs

RUN python3 -m compileall -q /app

EXPOSE 8080
HEALTHCHECK --interval=2m --timeout=10s --start-period=90s --retries=3 \
  CMD python3 -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/health',timeout=8)" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "run.py"]
