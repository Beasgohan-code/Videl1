FROM python:3.12-slim

# Prevent Python from buffering logs
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV MALLOC_ARENA_MAX=2
ENV MALLOC_TRIM_THRESHOLD_=65536

# System dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ffmpeg \
       unzip \
       gcc \
       git \
       curl \
       procps \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*


COPY . /app/music_worker/

WORKDIR /app

ENV DENO_INSTALL=/usr/local/deno
RUN mkdir -p $DENO_INSTALL && \
    curl -fsSL https://deno.land/install.sh | sh
ENV PATH=$DENO_INSTALL/bin:$PATH


# Copy dependency file first (Docker layer cache)

# Upgrade pip & install deps
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/music_worker/requirements.txt



CMD ["sh", "-c", "PYTHONPATH=/app MUSIC_WORKER_STANDALONE=1 python3 -m music_worker"]
