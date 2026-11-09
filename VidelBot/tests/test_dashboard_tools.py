"""Dashboard toolkit is shipped by the existing Studio routes; no live services required."""
from tests.harness import run

from webapp.api_routes import serve_app_css, serve_app_js


def test_served_dashboard_assets_include_toolkit_and_theme_fix():
    async def check():
        js = await serve_app_js(None)
        css = await serve_app_css(None)
        assert js.content_type == "application/javascript"
        assert css.content_type == "text/css"
        assert "window.VidelTools" in js.text
        assert 'async everyday_tools(vp)' in js.text
        assert 'data-goto="everyday_tools"' in js.text
        assert ".tool-grid" in css.text
        # The late dark palette must not override the root when light is selected.
        assert ':root, [data-theme="dark"] { --bg-app:#0d1220;' not in css.text
    run(check())
