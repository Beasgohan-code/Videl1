"""
Google Drive API v3 client wrapper with headless auth safety and explicit HTTP timeouts.

Supports:
  • Service Account JSON (`config.GDRIVE_SA_FILE` / `GOOGLE_APPLICATION_CREDENTIALS`)
  • Authorized OAuth2 user token JSON (`config.GDRIVE_TOKEN_FILE`, default `token.json`)

Never uses unsafe `pickle.load` and never launches an interactive browser OAuth server
inside headless production containers (raises `DriveNotConfiguredError` cleanly instead).
"""
from __future__ import annotations

import logging
import os
import re
import socket
import urllib.parse as urlparse
from urllib.parse import parse_qs

import config

log = logging.getLogger("videl.drive")
logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
logging.getLogger("googleapiclient.discovery").setLevel(logging.WARNING)

G_DRIVE_DIR_MIME_TYPE = "application/vnd.google-apps.folder"
G_DRIVE_FILE_LINK = "https://drive.google.com/open?id={}"
G_DRIVE_FOLDER_LINK = "https://drive.google.com/drive/folders/{}"
SCOPES = ["https://www.googleapis.com/auth/drive"]
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,128}$")
TRANSIENT_HTTP_CODES = frozenset({429, 500, 502, 503, 504})


class DriveNotConfiguredError(RuntimeError):
    """Raised when Google Drive transfer is requested without valid OAuth/SA credentials."""


def is_drive_configured() -> bool:
    """Return True if either a Service Account JSON or OAuth `token.json` is present on disk."""
    sa_file = (getattr(config, "GDRIVE_SA_FILE", "") or "").strip()
    token_file = (getattr(config, "GDRIVE_TOKEN_FILE", "token.json") or "token.json").strip()
    return (bool(sa_file) and os.path.isfile(sa_file)) or (bool(token_file) and os.path.isfile(token_file))


def _get_file_id(link: str) -> str | None:
    """Extract and validate a Google Drive file/folder ID from a share URL or raw ID."""
    text = (link or "").strip()
    if not text:
        return None
    if _SAFE_ID_RE.match(text):
        return text
    if "folders" in text or "file" in text:
        regex = r"https?://drive\.google\.com/(?:drive)?/?u?/?\d?/?(?:mobile)?/?(?:file)?(?:folders)?/?d?/([-\w]+)"
        res = re.search(regex, text)
        if res:
            cand = res.group(1)
            return cand if _SAFE_ID_RE.match(cand) else None
    try:
        parsed = urlparse.urlparse(text)
        vals = parse_qs(parsed.query).get("id") or []
        if vals and _SAFE_ID_RE.match(vals[0]):
            return vals[0]
    except Exception:
        return None
    return None


def _build_drive_service(creds, timeout: int):
    from googleapiclient.discovery import build

    try:
        import google_auth_httplib2
        import httplib2

        authed_http = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http(timeout=timeout))
        return build("drive", "v3", http=authed_http, cache_discovery=False)
    except ImportError:
        if socket.getdefaulttimeout() is None:
            socket.setdefaulttimeout(timeout)
        return build("drive", "v3", credentials=creds, cache_discovery=False)


class DriveAPI:
    SCOPES = SCOPES

    def __init__(self, *, service=None):
        self.creds = None
        if service is not None:
            self.service = service
            return

        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
        from google.oauth2.credentials import Credentials

        sa_file = (getattr(config, "GDRIVE_SA_FILE", "") or "").strip()
        token_file = (getattr(config, "GDRIVE_TOKEN_FILE", "token.json") or "token.json").strip()
        timeout = max(10, int(getattr(config, "DRIVE_HTTP_TIMEOUT", 60) or 60))

        if sa_file and os.path.isfile(sa_file):
            self.creds = service_account.Credentials.from_service_account_file(sa_file, scopes=SCOPES)
        elif token_file and os.path.isfile(token_file):
            self.creds = Credentials.from_authorized_user_file(token_file, SCOPES)
            if self.creds and self.creds.expired and self.creds.refresh_token:
                self.creds.refresh(Request())
                try:
                    with open(token_file, "w", encoding="utf-8") as token_fh:
                        token_fh.write(self.creds.to_json())
                except OSError as exc:
                    log.warning("Could not persist refreshed Drive token: %s", exc)

        if not self.creds or (not getattr(self.creds, "valid", True) and not sa_file):
            raise DriveNotConfiguredError(
                "Google Drive is not configured. Provide valid OAuth `token.json` or `GDRIVE_SA_FILE`."
            )

        self.service = _build_drive_service(self.creds, timeout=timeout)

    def listFolders(self):
        try:
            page_token = None
            folders = []
            while True:
                response = (
                    self.service.files()
                    .list(
                        q="mimeType='application/vnd.google-apps.folder' and trashed=false",
                        spaces="drive",
                        fields="nextPageToken, files(id, name)",
                        supportsAllDrives=True,
                        includeItemsFromAllDrives=True,
                        pageToken=page_token,
                    )
                    .execute()
                )
                for item in response.get("files", []):
                    folders.append((item.get("name"), item.get("id")))
                page_token = response.get("nextPageToken")
                if page_token is None:
                    break
            return folders
        except Exception as e:
            log.warning("Drive listFolders failed: %s", e)
            return None
