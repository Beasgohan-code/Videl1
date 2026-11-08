"""Removal checks do not imply live worker rollout or database deletion."""
import json
from pathlib import Path

from tests.harness import load_all

load_all()


def test_removed_plugins_are_not_discoverable_or_advertised():
    from core.commands import ADMIN_COMMANDS, GROUP_COMMANDS, OWNER_COMMANDS, USER_COMMANDS
    from core.middleware import _ANON_GROUP_MOD_CMDS
    from core.plugin_manager import plugin_manager

    removed_plugins = {"group_activity", "group_insights"}
    removed_commands = {"rankings", "mystats", "topusers", "topgroups", "policysim", "ghealth", "activity", "topmembers", "myactivity", "memberactivity", "groupactivity", "activityprivacy"}
    discovered = plugin_manager.discover()
    assert removed_plugins.isdisjoint(discovered)
    assert "group_mod" in discovered
    index = json.loads(Path("plugins/index.json").read_text(encoding="utf-8"))
    assert removed_plugins.isdisjoint(p["name"] for p in index["plugins"])
    assert "group_mod" in {p["name"] for p in index["plugins"]}
    advertised = {name for group in (USER_COMMANDS, GROUP_COMMANDS, ADMIN_COMMANDS, OWNER_COMMANDS) for name, _ in group}
    assert removed_commands.isdisjoint(advertised)
    assert removed_commands.isdisjoint(_ANON_GROUP_MOD_CMDS)
    assert not any((Path("plugins") / name).exists() for name in removed_plugins)
