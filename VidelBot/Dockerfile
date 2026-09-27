FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    TZ="Asia/Kolkata"

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg mediainfo git wget gcc python3-dev \
        p7zip-full unzip unrar-free \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x extract

EXPOSE 8080
HEALTHCHECK --interval=2m --timeout=10s --start-period=90s --retries=3 \
  CMD python3 -c "import os,urllib.request;urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT','8080')}/health\",timeout=8)" || exit 1
CMD ["python3", "run.py"]
