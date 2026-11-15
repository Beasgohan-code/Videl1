"""
Safe Archive, Staged Security Review & Owner-Approval Plugin Installer
(inspired by WZML-X wzv3-dev).

Installs validated YAML-manifest plugins into ``plugin_manager.plugins_dir`` from:
  • local ``.zip`` / ``.tar`` / ``.tar.gz`` / ``.tgz`` archives (e.g. Telegram uploads)
  • direct HTTPS archive URLs (guarded against SSRF + size-capped)
  • GitHub shorthand ``gh:owner/repo[@ref][:subdir]`` or ``https://github.com/owner/repo``
  • remote ``index.json`` plugin catalogs

Trust boundary & staged approval workflow:
  • Interactive / remote installs run through ``stage_from_archive`` /
    ``stage_from_source`` first — NO plugin Python code is imported or executed
    during staging.
  • Staging computes SHA-256, validates the YAML manifest, checks missing
    dependencies & command collisions, parses every ``.py`` file with ``ast.parse``
    (rejecting syntax errors and reporting static capability flags such as
    ``subprocess``, ``eval/exec``, ``os.system``, ``ctypes``, ``env/token``), and
    waits for explicit **bot owner** approval (``approve_staged``) before moving
    into ``plugins/<name>/``.
"""
from __future__ import annotations

import ast
import hashlib
import logging
import os
import re
import secrets
import shutil
import tarfile
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp

import config
from core.plugin_manager import (
    MANIFEST_NAMES,
    PluginManifest,
    find_manifest_file,
    missing_dependencies,
    plugin_manager,
)
from VideoEncoder.utils.leech import check_public

log = logging.getLogger("videl.plugin_installer")

MAX_ARCHIVE_BYTES = 50 * 1024 * 1024        # 50 MB compressed
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024  # 100 MB uncompressed
MAX_ARCHIVE_FILES = 200
STAGE_TTL_SEC = 900                         # 15 minutes


class PluginInstallError(Exception):
    """Raised when an archive or source fails safety, syntax, or manifest validation."""


@dataclass
class StagedPlugin:
    stage_id: str
    manifest: PluginManifest
    stage_root: Path
    plugin_dir: Path
    sha256: str
    source_label: str
    py_files: int = 0
    total_bytes: int = 0
    risk_flags: list[str] = field(default_factory=list)
    missing_deps: list[str] = field(default_factory=list)
    conflicts: list[tuple[str, str]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


_STAGED: dict[str, StagedPlugin] = {}

_RISK_MODULES = {
    "subprocess": "subprocess",
    "pty": "pty",
    "ctypes": "ctypes",
    "cffi": "native-ffi",
    "socket": "raw-socket",
}
_RISK_CALLS = {
    "eval": "eval()",
    "exec": "exec()",
    "compile": "compile()",
    "__import__": "dynamic-import",
}
_SENSITIVE_ATTRS = {"BOT_TOKEN", "ENCRYPTION_KEY", "DB_URI", "API_HASH", "SESSION_STRING"}


def sha256_digest(file_path: str | Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as fp:
        for chunk in iter(lambda: fp.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def inspect_plugin_tree(plugin_dir: Path) -> tuple[int, int, list[str]]:
    """Parse every ``.py`` file with ``ast.parse`` (zero execution) and summarize risk flags."""
    py_count = 0
    total_bytes = 0
    flags: set[str] = set()

    for root, _, files in os.walk(plugin_dir):
        for fname in sorted(files):
            fpath = Path(root) / fname
            if fpath.is_symlink():
                raise PluginInstallError(f"Symlink blocked inside plugin tree: {fname}")
            total_bytes += fpath.stat().st_size
            if not fname.endswith(".py"):
                continue
            py_count += 1
            try:
                src = fpath.read_text(encoding="utf-8")
            except UnicodeDecodeError as e:
                raise PluginInstallError(f"Non-UTF8 Python file {fname!r}: {e}") from e
            try:
                tree = ast.parse(src, filename=fname)
            except SyntaxError as e:
                raise PluginInstallError(f"Syntax error in {fname}:{e.lineno}: {e.msg}") from e

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".", 1)[0]
                        if top in _RISK_MODULES:
                            flags.add(_RISK_MODULES[top])
                elif isinstance(node, ast.ImportFrom) and node.module:
                    top = node.module.split(".", 1)[0]
                    if top in _RISK_MODULES:
                        flags.add(_RISK_MODULES[top])
                elif isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name) and node.func.id in _RISK_CALLS:
                        flags.add(_RISK_CALLS[node.func.id])
                    elif isinstance(node.func, ast.Attribute):
                        if node.func.attr in ("system", "popen", "create_subprocess_shell", "create_subprocess_exec"):
                            flags.add("shell/proc")
                elif isinstance(node, ast.Attribute) and node.attr in _SENSITIVE_ATTRS:
                    flags.add(f"reads:{node.attr}")
                elif isinstance(node, ast.Name) and node.id in _SENSITIVE_ATTRS:
                    flags.add(f"reads:{node.id}")

    return py_count, total_bytes, sorted(flags)


def _validate_rel_path(member_name: str) -> Path:
    """Ensure archive member path is strictly relative and free of traversal."""
    norm = member_name.replace("\\", "/")
    if not norm or norm.startswith("/") or norm.startswith("~") or re.match(r"^[A-Za-z]:", norm):
        raise PluginInstallError(f"Unsafe archive path: {member_name!r}")
    parts = [p for p in norm.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise PluginInstallError(f"Path traversal blocked in archive: {member_name!r}")
    return Path(*parts) if parts else Path(".")


def extract_safe_archive(archive_path: str | Path, dest_dir: str | Path) -> Path:
    """Validate and extract a ``.zip`` or ``.tar*`` archive into *dest_dir*."""
    src = Path(archive_path)
    dst = Path(dest_dir).resolve()
    if not src.is_file():
        raise PluginInstallError("Archive file does not exist.")
    size = src.stat().st_size
    if size <= 0 or size > MAX_ARCHIVE_BYTES:
        raise PluginInstallError(f"Archive size ({size} B) exceeds 50 MB safety cap.")

    name_lower = src.name.lower()
    dst.mkdir(parents=True, exist_ok=True)

    if zipfile.is_zipfile(src):
        with zipfile.ZipFile(src, "r") as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ARCHIVE_FILES:
                raise PluginInstallError(f"Archive has too many entries ({len(infos)} > {MAX_ARCHIVE_FILES}).")
            total = 0
            for info in infos:
                rel = _validate_rel_path(info.filename)
                unix_mode = (info.external_attr >> 16) & 0o170000
                if unix_mode == 0o120000:
                    raise PluginInstallError(f"Symlink blocked in zip archive: {info.filename!r}")
                total += max(0, info.file_size)
                if total > MAX_UNCOMPRESSED_BYTES:
                    raise PluginInstallError("Archive uncompressed size exceeds 100 MB safety cap.")
                target = (dst / rel).resolve()
                if dst not in (target, *target.parents):
                    raise PluginInstallError(f"Archive member escapes target directory: {info.filename!r}")
            for info in infos:
                rel = _validate_rel_path(info.filename)
                target = dst / rel
                if info.filename.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info, "r") as rfp, open(target, "wb") as wfp:
                        shutil.copyfileobj(rfp, wfp)
        return dst

    if name_lower.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")) or tarfile.is_tarfile(src):
        with tarfile.open(src, "r:*") as tf:
            members = tf.getmembers()
            if len(members) > MAX_ARCHIVE_FILES:
                raise PluginInstallError(f"Archive has too many entries ({len(members)} > {MAX_ARCHIVE_FILES}).")
            total = 0
            for m in members:
                if m.issym() or m.islnk() or m.ischr() or m.isblk() or m.isfifo():
                    raise PluginInstallError(f"Special/symlink tar entry blocked: {m.name!r}")
                rel = _validate_rel_path(m.name)
                total += max(0, m.size)
                if total > MAX_UNCOMPRESSED_BYTES:
                    raise PluginInstallError("Archive uncompressed size exceeds 100 MB safety cap.")
                target = (dst / rel).resolve()
                if dst not in (target, *target.parents):
                    raise PluginInstallError(f"Archive member escapes target directory: {m.name!r}")
            for m in members:
                rel = _validate_rel_path(m.name)
                target = dst / rel
                if m.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif m.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    ef = tf.extractfile(m)
                    if ef is not None:
                        with ef, open(target, "wb") as wfp:
                            shutil.copyfileobj(ef, wfp)
        return dst

    raise PluginInstallError("Unsupported archive format (use .zip or .tar.gz).")


def _locate_plugin_root(extracted: Path, subdir: str = "") -> tuple[Path, PluginManifest]:
    """Find ``plugin.yaml`` / ``wzml_plugin.yml`` inside *extracted* and validate it."""
    search_root = (extracted / subdir).resolve() if subdir else extracted.resolve()
    if extracted.resolve() not in (search_root, *search_root.parents):
        raise PluginInstallError("Invalid plugin subdirectory.")
    if not search_root.is_dir():
        raise PluginInstallError(f"Subdirectory {subdir!r} not found in archive.")

    yml = find_manifest_file(search_root)
    if yml is None:
        # Walk up to 2 nested levels (e.g. GitHub repo tarball root/plugins/<name>)
        queue = [(search_root, 0)]
        while queue and yml is None:
            cur, depth = queue.pop(0)
            if depth >= 2:
                continue
            for child in sorted(cur.iterdir()):
                if child.is_dir() and not child.name.startswith((".", "__")):
                    found = find_manifest_file(child)
                    if found is not None:
                        yml = found
                        break
                    queue.append((child, depth + 1))

    if yml is None:
        raise PluginInstallError(f"No manifest ({MANIFEST_NAMES[0]}) found in archive.")
    try:
        manifest = PluginManifest.from_file(yml)
    except Exception as e:
        raise PluginInstallError(f"Invalid plugin manifest: {e}") from e
    entry_path = (yml.parent / manifest.entry).resolve()
    if yml.parent.resolve() not in (entry_path, *entry_path.parents) or not entry_path.is_file():
        raise PluginInstallError(f"Declared entry module {manifest.entry!r} does not exist in plugin.")
    return yml.parent, manifest


def resolve_github_source(spec: str) -> tuple[str, str]:
    """Convert ``gh:owner/repo[@ref][:subdir]`` or GitHub URL → (archive_url, subdir)."""
    s = spec.strip()
    if s.startswith("gh:"):
        body = s[3:].strip()
        subdir = ""
        if ":" in body:
            body, subdir = body.split(":", 1)
        ref = "HEAD"
        if "@" in body:
            body, ref = body.split("@", 1)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", body):
            raise PluginInstallError("Invalid GitHub spec (expected gh:owner/repo[@ref][:subdir]).")
        return f"https://codeload.github.com/{body}/tar.gz/{ref}", subdir.strip("/")

    parsed = urlparse(s)
    if parsed.hostname in ("github.com", "www.github.com"):
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and not s.endswith((".zip", ".tar.gz", ".tgz")):
            owner, repo = parts[0], parts[1].removesuffix(".git")
            ref = "HEAD"
            subdir = ""
            if len(parts) >= 4 and parts[2] == "tree":
                ref = parts[3]
                subdir = "/".join(parts[4:])
            return f"https://codeload.github.com/{owner}/{repo}/tar.gz/{ref}", subdir
    return s, ""


async def _download_archive(url: str, dst_file: Path) -> Path:
    await check_public(url)
    total = 0
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45)) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise PluginInstallError(f"Download failed with HTTP {resp.status}.")
            with open(dst_file, "wb") as fp:
                async for chunk in resp.content.iter_chunked(65536):
                    total += len(chunk)
                    if total > MAX_ARCHIVE_BYTES:
                        raise PluginInstallError("Remote archive exceeds 50 MB safety cap.")
                    fp.write(chunk)
    return dst_file


def sweep_staged(max_age: float = STAGE_TTL_SEC) -> int:
    """Remove expired staged plugin folders and memory tokens."""
    now = time.time()
    removed = 0
    for sid, item in list(_STAGED.items()):
        if now - item.created_at > max_age:
            _STAGED.pop(sid, None)
            shutil.rmtree(item.stage_root, ignore_errors=True)
            removed += 1
    staging_parent = plugin_manager.plugins_dir / ".staging"
    if staging_parent.is_dir():
        for sub in staging_parent.iterdir():
            try:
                if sub.is_dir() and now - sub.stat().st_mtime > max_age:
                    shutil.rmtree(sub, ignore_errors=True)
                    removed += 1
            except OSError:
                pass
    return removed


def get_staged(stage_id: str) -> StagedPlugin | None:
    sweep_staged()
    return _STAGED.get(stage_id)


def reject_staged(stage_id: str) -> bool:
    item = _STAGED.pop(stage_id, None)
    if item is None:
        return False
    shutil.rmtree(item.stage_root, ignore_errors=True)
    return True


async def stage_from_archive(archive_path: str | Path, subdir: str = "",
                             source_label: str = "archive", expect_sha256: str = "") -> StagedPlugin:
    """Extract, AST-validate, and stage a plugin WITHOUT executing any plugin code."""
    sweep_staged()
    digest = sha256_digest(archive_path)
    if expect_sha256 and digest.lower() != expect_sha256.strip().lower():
        raise PluginInstallError(f"SHA-256 mismatch (expected {expect_sha256[:12]}…, got {digest[:12]}…).")

    stage_id = secrets.token_hex(6)
    stage_root = (plugin_manager.plugins_dir / ".staging" / stage_id).resolve()
    try:
        extracted = extract_safe_archive(archive_path, stage_root / "unpacked")
        plugin_dir, manifest = _locate_plugin_root(extracted, subdir=subdir)
        py_files, total_bytes, risk_flags = inspect_plugin_tree(plugin_dir)
        conflicts = plugin_manager.command_conflicts(manifest)
        if conflicts:
            summary = ", ".join(f"/{c} ({owner})" for c, owner in conflicts)
            raise PluginInstallError(f"Command conflict with existing bot commands: {summary}")
        missing = missing_dependencies(manifest.dependencies)
        staged = StagedPlugin(
            stage_id=stage_id,
            manifest=manifest,
            stage_root=stage_root,
            plugin_dir=plugin_dir,
            sha256=digest,
            source_label=source_label,
            py_files=py_files,
            total_bytes=total_bytes,
            risk_flags=risk_flags,
            missing_deps=missing,
            conflicts=conflicts,
        )
        _STAGED[stage_id] = staged
        return staged
    except Exception:
        shutil.rmtree(stage_root, ignore_errors=True)
        raise


async def stage_from_source(source: str) -> StagedPlugin:
    """Download & stage a remote URL, ``gh:owner/repo``, or catalog entry for owner review."""
    src = (source or "").strip()
    if not src:
        raise PluginInstallError("Empty plugin source.")
    if os.path.isfile(src):
        return await stage_from_archive(src, source_label=os.path.basename(src))

    expect_sha = ""
    if re.fullmatch(r"[a-z][a-z0-9_]{1,48}", src):
        catalog = await fetch_catalog()
        match = next((p for p in catalog if (p.get("name") or p.get("id")) == src), None)
        if not match:
            raise PluginInstallError(f"Plugin {src!r} not found in remote catalog.")
        dl_url = match.get("download_url") or match.get("url") or match.get("repo") or ""
        subdir = match.get("subdir") or f"plugins/{src}"
        expect_sha = str(match.get("sha256") or "")
        if not dl_url:
            dl_url = "https://codeload.github.com/SilentDemonSD/WZML-X/tar.gz/wzv3-dev"
        src_url, gh_sub = resolve_github_source(dl_url)
        subdir = gh_sub or subdir
    else:
        src_url, subdir = resolve_github_source(src)

    with tempfile.TemporaryDirectory(prefix="videl_plg_dl_") as tmp:
        ext = ".zip" if src_url.lower().endswith(".zip") else ".tar.gz"
        archive_file = Path(tmp) / f"plugin{ext}"
        await _download_archive(src_url, archive_file)
        return await stage_from_archive(
            archive_file,
            subdir=subdir,
            source_label=src[:80],
            expect_sha256=expect_sha,
        )


async def _activate_plugin_dir(plugin_src: Path, manifest: PluginManifest, client=None) -> PluginManifest:
    plugins_root = plugin_manager.plugins_dir
    plugins_root.mkdir(parents=True, exist_ok=True)
    target_dir = plugins_root / manifest.name
    with tempfile.TemporaryDirectory(prefix="videl_plg_act_") as tmp:
        backup_dir = Path(tmp) / "backup_prev"
        if target_dir.exists():
            await plugin_manager.unload_plugin(manifest.name, client=client)
            shutil.move(str(target_dir), str(backup_dir))
        try:
            shutil.copytree(str(plugin_src), str(target_dir))
            plugin_manager.discover()
            loaded = await plugin_manager.load_plugin(
                manifest.name,
                client=client,
                register_handlers=client is not None,
            )
            await plugin_manager.enable(manifest.name, client=client)
            return loaded
        except Exception as e:
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            if backup_dir.exists():
                shutil.move(str(backup_dir), str(target_dir))
                plugin_manager.discover()
            raise PluginInstallError(f"Plugin activation failed: {e}") from e


async def approve_staged(stage_id: str, owner_id: int, client=None) -> PluginManifest:
    """Commit a staged plugin after explicit owner verification (`owner_id in config.OWNERS`)."""
    if owner_id not in config.OWNERS:
        raise PermissionError("Only bot owners can approve and install staged plugins.")
    staged = get_staged(stage_id)
    if staged is None:
        raise PluginInstallError("Staged plugin expired or does not exist.")
    if staged.missing_deps:
        raise PluginInstallError(
            f"Missing Python dependencies ({', '.join(staged.missing_deps)}); install them in the host environment first."
        )
    _STAGED.pop(stage_id, None)
    try:
        return await _activate_plugin_dir(staged.plugin_dir, staged.manifest, client=client)
    finally:
        shutil.rmtree(staged.stage_root, ignore_errors=True)


async def install_from_archive(archive_path: str | Path, subdir: str = "", client=None) -> PluginManifest:
    """Programmatic helper: stage, validate, and activate a local archive immediately."""
    staged = await stage_from_archive(archive_path, subdir=subdir, source_label=str(archive_path))
    _STAGED.pop(staged.stage_id, None)
    try:
        if staged.missing_deps:
            raise PluginInstallError(f"Missing Python dependencies: {', '.join(staged.missing_deps)}")
        return await _activate_plugin_dir(staged.plugin_dir, staged.manifest, client=client)
    finally:
        shutil.rmtree(staged.stage_root, ignore_errors=True)


async def install_from_source(source: str, client=None) -> PluginManifest:
    """Programmatic helper: download, validate, and activate a plugin source."""
    staged = await stage_from_source(source)
    _STAGED.pop(staged.stage_id, None)
    try:
        if staged.missing_deps:
            raise PluginInstallError(f"Missing Python dependencies: {', '.join(staged.missing_deps)}")
        return await _activate_plugin_dir(staged.plugin_dir, staged.manifest, client=client)
    finally:
        shutil.rmtree(staged.stage_root, ignore_errors=True)


async def uninstall_plugin(name: str, client=None) -> bool:
    """Unload and remove an installed plugin folder."""
    plugin_manager.discover()
    manifest = plugin_manager.manifests.get(name)
    if manifest is None or manifest.path is None:
        raise PluginInstallError(f"Plugin {name!r} is not installed.")
    await plugin_manager.unload_plugin(name, client=client)
    shutil.rmtree(manifest.path, ignore_errors=True)
    await plugin_manager.sync_db()
    plugin_manager.discover()
    return True


async def fetch_catalog(index_url: str | None = None) -> list[dict[str, Any]]:
    """Fetch remote plugin registry ``index.json`` (best-effort)."""
    url = (index_url or config.PLUGIN_INDEX_URL or "").strip()
    if not url:
        return []
    try:
        await check_public(url)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json(content_type=None)
                if isinstance(data, dict):
                    items = data.get("plugins") or []
                elif isinstance(data, list):
                    items = data
                else:
                    items = []
                return [x for x in items if isinstance(x, dict) and (x.get("name") or x.get("id"))]
    except Exception as e:
        log.debug(f"plugin catalog fetch failed: {e}")
        return []
