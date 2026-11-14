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
  apt_common=(
    -o Dir::Etc::sourceparts=-
    -o Acquire::Retries=1
    -o Acquire::http::Timeout=30
    -o Acquire::https::Timeout=30
  )
  # Keep retries bounded. If the runner's configured mirror is unavailable,
  # retry against signed, official Ubuntu 24.04 archives. Never use stale lists.
  if timeout --signal=TERM --kill-after=10s 120s sudo apt-get \
      -o "Dir::Etc::sourcelist=$sources" "${apt_common[@]}" -o APT::Update::Error-Mode=any update; then
    echo "Ubuntu runner package index updated."
  else
    rc=$?
    echo "::warning title=Runner Ubuntu index update failed::apt update exited $rc; retrying the official Ubuntu 24.04 archives."
    fallback_dir=$(mktemp -d)
    trap 'rm -f "$fallback_dir/ubuntu.sources"; rmdir "$fallback_dir"' EXIT
    sources="$fallback_dir/ubuntu.sources"
    # The Ubuntu archive keyring authenticates Release files as usual. This file
    # is temporary and never changes system or production apt configuration.
    printf '%s\n' \
      'Types: deb' \
      'URIs: https://archive.ubuntu.com/ubuntu' \
      'Suites: noble noble-updates noble-backports noble-security' \
      'Components: main restricted universe multiverse' \
      'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg' > "$sources"
    if timeout --signal=TERM --kill-after=10s 120s sudo apt-get \
        -o "Dir::Etc::sourcelist=$sources" "${apt_common[@]}" -o APT::Update::Error-Mode=any update; then
      echo "Official Ubuntu package index updated."
    else
      rc=$?
      echo "::error title=Ubuntu package index unavailable::Official archive apt update exited $rc; native checks cannot continue."
      exit "$rc"
    fi
  fi
  if timeout --signal=TERM --kill-after=10s 240s sudo env DEBIAN_FRONTEND=noninteractive apt-get \
      -o "Dir::Etc::sourcelist=$sources" "${apt_common[@]}" install -y --no-install-recommends \
      ffmpeg mediainfo mkvtoolnix p7zip-full git; then
    echo "Native packages installed."
  else
    rc=$?
    echo "::error title=Native package install failed::apt install exited $rc; the native check is incomplete."
    exit "$rc"
  fi
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
