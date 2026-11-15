"""Guard the explicitly retired calculator, Studio tools page, and Overview customizer."""

import asyncio
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from core import commands
from core.plugin_manager import PluginBase, plugin_manager
from plugins.widget import plugin, transport
from webapp import api_routes, db_models, services_ops, ui_client, ui_styles
import keep_alive


def test_telegram_snapshot_plugin_has_no_calculator_command_or_poll_listener():
    manifest = plugin_manager.discover()["widget"]
    assert manifest.commands == []
    assert issubclass(plugin.Plugin, PluginBase)
    assert callable(transport.request)
    assert not hasattr(transport, "poll_widget_queries")
    assert not hasattr(plugin, "widget_command")
    assert not hasattr(plugin, "handle_widget_query")
    assert not hasattr(plugin, "ensure_query_listener")
    menu_commands = {name for name, _ in commands.USER_COMMANDS + commands.GROUP_COMMANDS}
    assert "widget" not in menu_commands
    admin_commands = {name for name, _ in commands.ADMIN_COMMANDS}
    assert "dashboard" in admin_commands
    assert callable(plugin._primary_bot)
    assert callable(plugin._beta_enabled)


def test_everyday_tools_and_custom_overview_widgets_are_not_served():
    client = ui_client.CLIENT_JS
    css = ui_styles.STYLES_CSS
    assert "everyday_tools" not in client
    assert "studio_tools" not in client
    assert "Everyday Tools" not in client
    assert "Customize Widgets" not in client
    assert "dash-customizer" not in client
    assert "dashboard-layout" not in client
    assert "Workspace Activity" in client
    assert "studio_tools" not in css
    assert ".tool-grid" not in css


def test_custom_dashboard_category_is_removed_from_companion_template_studio():
    companion = (
        Path(__file__).resolve().parents[2]
        / "frontend-platform/apps/studio/components/TemplateStudio.tsx"
    ).read_text(encoding="utf-8")
    assert "dashboard_layout" not in companion
    assert "notification_prefs" in companion


def test_custom_dashboard_api_and_storage_surface_are_removed():
    assert not hasattr(api_routes, "api_dashboard_layout")
    assert not hasattr(services_ops, "DASHBOARD_WIDGETS_CATALOG")
    assert not hasattr(services_ops, "get_dashboard_layout")
    assert not hasattr(services_ops, "save_dashboard_layout")
    assert not hasattr(db_models, "dashboards_col")
    assert "dashboard_layout" not in services_ops.TEMPLATE_CATEGORIES


def test_removed_dashboard_layout_endpoint_returns_not_found():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            response = await client.get("/api/v1/workspaces/unknown/dashboard-layout")
            assert response.status == 404

    asyncio.run(go())
