#!/usr/bin/env bash
# GitHub's Ubuntu 24.04 image has many apt repositories; the test tools come
# from Ubuntu only. Limit update/install to those sources, not third-party feeds.
set -euo pipefail

sources=${1:-/etc/apt/sources.list.d/ubuntu.sources}
tools=(ffmpeg ffprobe mediainfo mkvmerge 7z git)
missing=()
for tool in "${tools[@]}"; do
  command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
done

if ((${#missing[@]})); then
  printf 'Missing native tools: %s\n' "${missing[*]}"
  if [[ ! -r "$sources" ]]; then
    printf 'Ubuntu apt source file is missing or unreadable: %s\n' "$sources" >&2
    exit 1
  fi
  apt_options=(
    -o "Dir::Etc::sourcelist=$sources"
    -o Dir::Etc::sourceparts=-
    -o Acquire::Retries=1
    -o Acquire::http::Timeout=30
    -o Acquire::https::Timeout=30
  )
  # Explicit command limits and error-on-any-source-failure make a network or
  # repository outage visible rather than silently using stale package lists.
  timeout --signal=TERM --kill-after=10s 240s sudo apt-get "${apt_options[@]}" -o APT::Update::Error-Mode=any update
  timeout --signal=TERM --kill-after=10s 240s sudo env DEBIAN_FRONTEND=noninteractive apt-get "${apt_options[@]}" install -y --no-install-recommends \
    ffmpeg mediainfo mkvtoolnix p7zip-full git
fi

# Do not let missing tools silently turn real-media tests into skips.
for tool in "${tools[@]}"; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    printf 'Required native tool unavailable after setup: %s\n' "$tool" >&2
    exit 1
  fi
done
ffmpeg -version
ffprobe -version
