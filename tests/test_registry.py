from tools.registry import ToolRegistry


def test_registry_loads_tools():
    registry = ToolRegistry()
    registry.load_all()
    names = {meta.name for meta in registry.list_available()}
    assert "fetch_html" in names
    assert "render_js" in names
    assert "audit_deals" in names
