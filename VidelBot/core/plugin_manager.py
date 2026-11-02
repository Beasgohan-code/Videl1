"""
Modular YAML-manifest Plugin Manager (inspired by WZML-X wzv3-dev).

Discovers, validates, loads, configures, enables/disables, and hot-reloads
plugins stored under ``config.PLUGINS_DIR`` (``VidelBot/plugins/<name>/``).
Supports both ``plugin.yaml`` / ``plugin.yml`` and WZML-X's ``wzml_plugin.yml`` /
``wzml_plugin.yaml`` manifests (including WZML-X key aliases, ``config_schema``,
``callbacks``, ``tags``, ``min_bot_version`` / ``max_bot_version``, and
dependency checks via ``importlib.metadata``).

Plugin enabled/disabled overrides and per-plugin configuration are persisted in
MongoDB (``vdb.db["plugins"]`` and ``vdb.db["plugin_settings"]``) and cached in
memory so ``plugin_enabled(name)`` checks run in O(1) without DB round-trips on
incoming messages.
"""
from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pyrogram import filters

import config
from core.db import vdb

log = logging.getLogger("videl.plugins")

BOT_VERSION = "3.2.0"
MANIFEST_NAMES = ("plugin.yaml", "plugin.yml", "wzml_plugin.yml", "wzml_plugin.yaml")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,48}$")
SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")
KEY_ALIASES = {
    "main_file": "entry",
    "min_wzml_version": "min_bot_version",
    "max_wzml_version": "max_bot_version",
    "settings_schema": "config_schema",
    "deps": "dependencies",
    "python_dependencies": "dependencies",
    "repository": "homepage",
}
ACCESS_ALIASES = {
    "user": "user",
    "authorized": "user",
    "authorized_uset": "user",
    "admin": "admin",
    "sudo": "admin",
    "owner": "owner",
}


def parse_semver(ver: str) -> tuple[int, int, int]:
    m = SEMVER_RE.match((ver or "").strip())
    if not m:
        return (0, 0, 0)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def semver_ge(actual: str, required: str) -> bool:
    if not required:
        return True
    return parse_semver(actual) >= parse_semver(required)


def semver_le(actual: str, maximum: str) -> bool:
    if not maximum:
        return True
    return parse_semver(actual) <= parse_semver(maximum)


def dist_name(spec: str) -> str:
    text = str(spec or "").strip()
    for sep in ("[", ";", "=", ">", "<", "!", "~", " "):
        text = text.split(sep, 1)[0]
    return text.strip()


def missing_dependencies(specs: list[str] | None) -> list[str]:
    """Return requirement specs whose distribution is not installed in this environment."""
    missing: list[str] = []
    for spec in specs or []:
        pkg = dist_name(spec)
        if not pkg:
            continue
        try:
            importlib.metadata.distribution(pkg)
        except importlib.metadata.PackageNotFoundError:
            missing.append(spec)
    return missing


def coerce_config_value(raw_val: Any, schema_entry: dict[str, Any] | None = None) -> Any:
    """Coerce a string/CLI setting value according to ``config_schema`` entry type."""
    kind = str((schema_entry or {}).get("type", "")).lower()
    if kind in ("bool", "boolean"):
        if isinstance(raw_val, bool):
            return raw_val
        s = str(raw_val).strip().lower()
        if s in ("1", "true", "yes", "on", "y"):
            return True
        if s in ("0", "false", "no", "off", "n"):
            return False
        raise ValueError(f"Expected boolean value, got {raw_val!r}")
    if kind in ("int", "integer"):
        return int(raw_val)
    if kind in ("float", "number"):
        return float(raw_val)
    return raw_val


@dataclass
class PluginManifest:
    name: str
    version: str = "1.0.0"
    author: str = "Videl"
    description: str = ""
    commands: list[dict[str, Any]] = field(default_factory=list)
    callbacks: list[dict[str, str]] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    min_bot_version: str = ""
    max_bot_version: str = ""
    homepage: str = ""
    icon: str = ""
    license: str = ""
    tags: list[str] = field(default_factory=list)
    config_schema: dict[str, Any] = field(default_factory=dict)
    entry: str = "plugin.py"
    enabled_by_default: bool = True
    path: Path | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any], path: Path | None = None) -> "PluginManifest":
        if not isinstance(data, dict):
            raise ValueError("plugin.yaml root must be a YAML mapping")
        norm: dict[str, Any] = {}
        for k, v in data.items():
            target_k = KEY_ALIASES.get(k, k)
            if target_k not in norm or norm[target_k] in (None, "", [], {}):
                norm[target_k] = v

        name = str(norm.get("name", "")).strip().lower()
        if not NAME_RE.match(name):
            raise ValueError(f"Invalid plugin name {name!r} (expected lowercase [a-z][a-z0-9_]{{1,48}})")
        version = str(norm.get("version", "1.0.0")).strip() or "1.0.0"
        min_ver = str(norm.get("min_bot_version", "")).strip()
        max_ver = str(norm.get("max_bot_version", "")).strip()
        if min_ver and not semver_ge(BOT_VERSION, min_ver):
            raise ValueError(f"Plugin {name!r} requires bot >= {min_ver} (current {BOT_VERSION})")
        if max_ver and not semver_le(BOT_VERSION, max_ver):
            raise ValueError(f"Plugin {name!r} supports bot <= {max_ver} (current {BOT_VERSION})")

        raw_cmds = norm.get("commands") or []
        cmds: list[dict[str, Any]] = []
        if isinstance(raw_cmds, dict):
            for k, v in raw_cmds.items():
                cmds.append({"name": str(k).lstrip("/"), "description": str(v or ""), "aliases": [], "access": "user"})
        elif isinstance(raw_cmds, list):
            for item in raw_cmds:
                if isinstance(item, str):
                    cmds.append({"name": item.lstrip("/"), "description": "", "aliases": [], "access": "user"})
                elif isinstance(item, dict) and item.get("name"):
                    raw_acc = str(item.get("access") or ("owner" if item.get("owner_only") else "admin" if item.get("sudo_only") else "user")).lower()
                    aliases = [str(a).strip().lstrip("/") for a in (item.get("aliases") or []) if str(a).strip()]
                    cmds.append({
                        "name": str(item["name"]).strip().lstrip("/"),
                        "description": str(item.get("description") or item.get("desc") or ""),
                        "usage": str(item.get("usage") or ""),
                        "aliases": aliases,
                        "access": ACCESS_ALIASES.get(raw_acc, "user"),
                    })

        callbacks: list[dict[str, str]] = []
        for cb in norm.get("callbacks") or []:
            if isinstance(cb, dict) and cb.get("pattern"):
                callbacks.append({
                    "pattern": str(cb["pattern"]),
                    "description": str(cb.get("description") or ""),
                    "access": ACCESS_ALIASES.get(str(cb.get("access") or "user").lower(), "user"),
                })

        entry = str(norm.get("entry") or "").strip()
        if not entry:
            if path and (path / "plugin.py").is_file():
                entry = "plugin.py"
            elif path and (path / f"{name}.py").is_file():
                entry = f"{name}.py"
            elif path and (path / "__init__.py").is_file():
                entry = "__init__.py"
            else:
                entry = "plugin.py"

        schema = norm.get("config_schema")
        if not isinstance(schema, dict):
            schema = {}

        return cls(
            name=name,
            version=version,
            author=str(norm.get("author", "Videl")).strip() or "Videl",
            description=str(norm.get("description", "")).strip(),
            commands=cmds,
            callbacks=callbacks,
            permissions=[str(x) for x in (norm.get("permissions") or [])],
            dependencies=[str(x) for x in (norm.get("dependencies") or [])],
            min_bot_version=min_ver,
            max_bot_version=max_ver,
            homepage=str(norm.get("homepage", "")).strip(),
            icon=str(norm.get("icon", "")).strip(),
            license=str(norm.get("license", "")).strip(),
            tags=[str(t) for t in (norm.get("tags") or [])],
            config_schema=schema,
            entry=entry,
            enabled_by_default=bool(norm.get("enabled", True)),
            path=path,
        )

    @classmethod
    def from_file(cls, yaml_path: Path) -> "PluginManifest":
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        return cls.from_dict(raw or {}, path=yaml_path.parent)

    @property
    def command_names(self) -> list[str]:
        seen: list[str] = []
        for c in self.commands:
            for item in [c.get("name", ""), *(c.get("aliases") or [])]:
                item = str(item).strip().lstrip("/")
                if item and item not in seen:
                    seen.append(item)
        return seen


def find_manifest_file(plugin_dir: Path) -> Path | None:
    for fn in MANIFEST_NAMES:
        cand = plugin_dir / fn
        if cand.is_file():
            return cand
    return None


class PluginBase:
    """Optional OOP lifecycle & settings base class for Videl / WZML-X modular plugins."""

    def __init__(self, manifest: PluginManifest):
        self.manifest = manifest
        self.name = manifest.name
        self.config: dict[str, Any] = {
            k: v.get("default")
            for k, v in (manifest.config_schema or {}).items()
            if isinstance(v, dict) and "default" in v
        }
        self._handlers: list[tuple[Any, int]] = []

    def register_handler(self, handler, group: int = 0) -> None:
        self._handlers.append((handler, group))

    def get_config(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    async def get_setting(self, key: str, default: Any = None) -> Any:
        doc = await vdb.db["plugin_settings"].find_one({"_id": f"{self.name}:{key}"})
        if doc and "value" in doc:
            return doc["value"]
        return self.config.get(key, default)

    async def set_setting(self, key: str, value: Any) -> None:
        self.config[key] = value
        await vdb.db["plugin_settings"].update_one(
            {"_id": f"{self.name}:{key}"},
            {"$set": {"plugin": self.name, "key": key, "value": value}},
            upsert=True,
        )

    async def on_load(self, client=None) -> None:
        pass

    async def on_unload(self, client=None) -> None:
        pass

    async def on_enable(self) -> None:
        pass

    async def on_disable(self) -> None:
        pass

    async def on_configure(self, config_map: dict[str, Any]) -> None:
        pass


def register_handler_once(client: Any, handler: Any, group: int = 0) -> bool:
    """Register `(handler, group)` on `client` at most once to prevent duplicate dispatch."""
    if client is None or not hasattr(client, "add_handler"):
        return False
    seen = getattr(client, "_videl_registered_handlers", None)
    if seen is None:
        seen = set()
        try:
            setattr(client, "_videl_registered_handlers", seen)
        except Exception:
            seen = None
    key = (id(handler), int(group))
    if seen is not None and key in seen:
        return False
    client.add_handler(handler, int(group))
    if seen is not None:
        seen.add(key)
    return True


def unregister_handler_tracked(client: Any, handler: Any, group: int = 0) -> None:
    """Remove `(handler, group)` from `client` and clear its duplicate-guard entry."""
    seen = getattr(client, "_videl_registered_handlers", None)
    if isinstance(seen, set):
        seen.discard((id(handler), int(group)))
    if client is not None and hasattr(client, "remove_handler"):
        try:
            client.remove_handler(handler, int(group))
        except Exception:
            pass


class PluginManager:
    """Central registry for discovering, toggling, configuring, and hot-reloading plugins."""

    def __init__(self, plugins_dir: str | Path | None = None):
        self._dir = Path(plugins_dir or config.PLUGINS_DIR)
        self.manifests: dict[str, PluginManifest] = {}
        self.modules: dict[str, Any] = {}
        self.instances: dict[str, PluginBase] = {}
        self._dynamic_handlers: dict[str, list[tuple[Any, int]]] = {}
        self._enabled: dict[str, bool] = {}
        self._configs: dict[str, dict[str, Any]] = {}
        self.load_errors: dict[str, str] = {}
        self.manifest_errors: dict[str, str] = {}

    @property
    def plugins_dir(self) -> Path:
        return Path(config.PLUGINS_DIR) if self._dir is None else self._dir

    def set_plugins_dir(self, path: str | Path) -> None:
        self._dir = Path(path)

    def discover(self) -> dict[str, PluginManifest]:
        """Scan ``plugins_dir`` for ``*/plugin.yaml`` (or ``wzml_plugin.yml``)."""
        found: dict[str, PluginManifest] = {}
        m_errors: dict[str, str] = {}
        base = self.plugins_dir
        if not base.is_dir():
            self.manifests = found
            self.manifest_errors = m_errors
            return found
        for sub in sorted(base.iterdir()):
            if not sub.is_dir() or sub.name.startswith((".", "_")):
                continue
            yml = find_manifest_file(sub)
            if yml is None:
                continue
            try:
                manifest = PluginManifest.from_file(yml)
                found[manifest.name] = manifest
                self._enabled.setdefault(manifest.name, manifest.enabled_by_default)
            except Exception as e:
                err_msg = f"{type(e).__name__}: {e}"
                m_errors[sub.name] = err_msg
                log.warning(f"Skipping invalid plugin manifest {yml}: {e}")
        self.manifests = found
        self.manifest_errors = m_errors
        return found

    def taken_commands(self, skip: str | None = None) -> dict[str, str]:
        """Map command name → owner module/plugin to detect collisions before install."""
        self.discover()
        taken: dict[str, str] = {}
        try:
            from core import commands as cmd_mod
            plugin_cmds = {
                c
                for name, m in self.manifests.items()
                for c in m.command_names
            }
            for grp in (cmd_mod.USER_COMMANDS, cmd_mod.GROUP_COMMANDS, cmd_mod.ADMIN_COMMANDS, cmd_mod.OWNER_COMMANDS):
                for cname, _ in grp:
                    if cname not in plugin_cmds:
                        taken[cname] = "core"
        except Exception:
            pass
        for name, m in self.manifests.items():
            if name == skip:
                continue
            for cname in m.command_names:
                taken[cname] = f"plugin:{name}"
        return taken

    def command_conflicts(self, manifest: PluginManifest) -> list[tuple[str, str]]:
        taken = self.taken_commands(skip=manifest.name)
        return [(c, taken[c]) for c in manifest.command_names if c in taken]

    def is_enabled(self, name: str) -> bool:
        if not config.PLUGINS_ENABLED:
            return False
        if name not in self.manifests:
            self.discover()
        return self._enabled.get(name, True)

    async def sync_db(self) -> None:
        """Hydrate persisted enabled/disabled flags and plugin settings from MongoDB."""
        self.discover()
        try:
            async for doc in vdb.db["plugins"].find({}):
                pname = doc.get("_id") or doc.get("name")
                if pname and "enabled" in doc:
                    self._enabled[str(pname)] = bool(doc["enabled"])
            async for sdoc in vdb.db["plugin_settings"].find({}):
                pname = sdoc.get("plugin")
                skey = sdoc.get("key")
                if pname and skey and "value" in sdoc:
                    self._configs.setdefault(str(pname), {})[str(skey)] = sdoc["value"]
        except Exception as e:
            log.debug(f"plugin db sync skipped: {e}")

    def get_plugin_config(self, name: str) -> dict[str, Any]:
        m = self.manifests.get(name)
        defaults = {
            k: v.get("default")
            for k, v in ((m.config_schema if m else None) or {}).items()
            if isinstance(v, dict) and "default" in v
        }
        defaults.update(self._configs.get(name) or {})
        return defaults

    async def configure_plugin(self, name: str, key: str, raw_value: Any) -> Any:
        """Validate & persist a plugin setting, notifying ``PluginBase.on_configure`` if loaded."""
        self.discover()
        m = self.manifests.get(name)
        if m is None:
            raise KeyError(f"Unknown plugin: {name}")
        schema_entry = (m.config_schema or {}).get(key)
        if m.config_schema and schema_entry is None:
            raise KeyError(f"Unknown config key {key!r} for plugin {name}")
        val = coerce_config_value(raw_value, schema_entry if isinstance(schema_entry, dict) else None)
        self._configs.setdefault(name, {})[key] = val
        await vdb.db["plugin_settings"].update_one(
            {"_id": f"{name}:{key}"},
            {"$set": {"plugin": name, "key": key, "value": val}},
            upsert=True,
        )
        inst = self.instances.get(name)
        if inst is not None:
            inst.config = self.get_plugin_config(name)
            await inst.on_configure(dict(inst.config))
        return val

    async def set_enabled(self, name: str, enabled: bool, client=None) -> bool:
        self.discover()
        if name not in self.manifests:
            raise KeyError(f"Unknown plugin: {name}")
        self._enabled[name] = bool(enabled)
        await vdb.db["plugins"].update_one(
            {"_id": name},
            {"$set": {"name": name, "enabled": bool(enabled), "version": self.manifests[name].version}},
            upsert=True,
        )
        inst = self.instances.get(name)
        if inst is not None:
            if enabled:
                await inst.on_enable()
            else:
                await inst.on_disable()
        if enabled and name not in self.modules:
            await self.load_plugin(name, client=client, register_handlers=client is not None)
        return self._enabled[name]

    async def enable(self, name: str, client=None) -> bool:
        return await self.set_enabled(name, True, client=client)

    async def disable(self, name: str, client=None) -> bool:
        return await self.set_enabled(name, False, client=client)

    def _module_for_manifest(self, manifest: PluginManifest):
        """Import or load a plugin's entry file."""
        assert manifest.path is not None
        entry_file = (manifest.path / manifest.entry).resolve()
        if manifest.path.resolve() not in (entry_file, *entry_file.parents):
            raise ValueError(f"Entry path escapes plugin directory: {manifest.entry}")
        if not entry_file.is_file():
            raise FileNotFoundError(f"Plugin entry file missing: {entry_file}")

        root_dir = Path(os.getcwd()).resolve()
        try:
            rel = entry_file.relative_to(root_dir)
            if rel.name == "__init__.py":
                mod_name = ".".join(rel.parent.parts)
            else:
                mod_name = ".".join(rel.with_suffix("").parts)
            if mod_name in sys.modules:
                return sys.modules[mod_name], False
            return importlib.import_module(mod_name), True
        except ValueError:
            mod_name = f"videl_ext_plugin_{manifest.name}"
            spec = importlib.util.spec_from_file_location(mod_name, str(entry_file))
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot create module spec for {entry_file}")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)
            return mod, True

    async def load_plugin(self, name: str, client=None, register_handlers: bool = False) -> PluginManifest:
        self.discover()
        manifest = self.manifests.get(name)
        if manifest is None:
            raise KeyError(f"Plugin {name!r} not found")
        try:
            mod, fresh = self._module_for_manifest(manifest)
            self.modules[name] = mod

            plugin_cls = getattr(mod, "Plugin", None)
            if isinstance(plugin_cls, type) and issubclass(plugin_cls, PluginBase) and plugin_cls is not PluginBase:
                inst = plugin_cls(manifest)
                inst.config = self.get_plugin_config(name)
                self.instances[name] = inst
                await inst.on_load(client)
            else:
                inst = None

            if register_handlers and client is not None and hasattr(client, "add_handler"):
                added: list[tuple[Any, int]] = []
                if fresh:
                    for attr_name in vars(mod):
                        obj = getattr(mod, attr_name)
                        hs = getattr(obj, "handlers", None)
                        if not isinstance(hs, list) or getattr(obj, "__module__", None) != mod.__name__:
                            continue
                        for h, g in hs:
                            if isinstance(g, int) and register_handler_once(client, h, g):
                                added.append((h, g))
                if inst is not None:
                    for h, g in inst._handlers:
                        if register_handler_once(client, h, g):
                            added.append((h, g))
                if added:
                    self._dynamic_handlers[name] = added
            self.load_errors.pop(name, None)
            return manifest
        except Exception as e:
            self.load_errors[name] = f"{type(e).__name__}: {e}"
            raise

    async def unload_plugin(self, name: str, client=None) -> bool:
        inst = self.instances.pop(name, None)
        if inst is not None:
            try:
                await inst.on_unload(client)
            except Exception as e:
                log.warning(f"Plugin {name} on_unload error: {e}")
        if client is not None and hasattr(client, "remove_handler"):
            for h, g in self._dynamic_handlers.pop(name, []):
                unregister_handler_tracked(client, h, g)
        else:
            self._dynamic_handlers.pop(name, None)
        mod = self.modules.pop(name, None)
        if mod is not None and mod.__name__.startswith("videl_ext_plugin_"):
            sys.modules.pop(mod.__name__, None)
        return mod is not None or inst is not None

    async def unload_all(self, client=None) -> int:
        """Cleanly unload all active plugins on shutdown."""
        count = 0
        for name in list(self.modules.keys() | self.instances.keys()):
            if await self.unload_plugin(name, client=client):
                count += 1
        return count

    async def reload_plugin(self, name: str, client=None) -> PluginManifest:
        self.discover()
        manifest = self.manifests.get(name)
        if manifest is None:
            raise KeyError(f"Plugin {name!r} not found")
        had_dynamic = name in self._dynamic_handlers
        await self.unload_plugin(name, client=client)
        for mod_key in list(sys.modules):
            if mod_key == f"plugins.{name}" or mod_key.startswith(f"plugins.{name}."):
                try:
                    importlib.reload(sys.modules[mod_key])
                except Exception as e:
                    log.warning(f"reload {mod_key} failed: {e}")
        return await self.load_plugin(name, client=client, register_handlers=had_dynamic)

    async def initialize(self, client=None) -> int:
        """Discover manifests, sync DB enabled flags/settings, and instantiate active plugins."""
        await self.sync_db()
        loaded = 0
        for name in sorted(self.manifests):
            if self.is_enabled(name):
                try:
                    await self.load_plugin(name, client=client, register_handlers=False)
                    loaded += 1
                except Exception as e:
                    self.load_errors[name] = f"{type(e).__name__}: {e}"
                    log.warning(f"Failed initializing plugin {name}: {e}")
        return loaded

    def summary(self) -> list[dict[str, Any]]:
        self.discover()
        out = []
        for name in sorted(self.manifests):
            m = self.manifests[name]
            out.append({
                "name": m.name,
                "version": m.version,
                "author": m.author,
                "description": m.description,
                "commands": m.command_names,
                "enabled": self.is_enabled(name),
                "loaded": name in self.modules,
                "error": self.load_errors.get(name, ""),
                "homepage": m.homepage,
                "dependencies": m.dependencies,
                "missing_dependencies": missing_dependencies(m.dependencies),
                "permissions": m.permissions,
                "tags": m.tags,
            })
        return out

    def health_report(self) -> dict[str, Any]:
        """Structured health report of modular plugins, load failures, and manifest errors."""
        items = self.summary()
        return {
            "total": len(items),
            "enabled": sum(1 for p in items if p["enabled"]),
            "loaded": sum(1 for p in items if p["loaded"]),
            "healthy": sum(1 for p in items if p["enabled"] and p["loaded"] and not p["error"]),
            "errors": dict(self.load_errors),
            "manifest_errors": dict(self.manifest_errors),
        }


plugin_manager = PluginManager()


def plugin_enabled(name: str) -> bool:
    return plugin_manager.is_enabled(name)


def plugin_filter(name: str):
    """WzGram message filter that passes only when plugin *name* is enabled."""
    async def _check(_, __, ___) -> bool:
        return plugin_manager.is_enabled(name)

    return filters.create(_check, f"PluginEnabled_{name}")
