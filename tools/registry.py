from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType

import tools as tools_pkg


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, callable] = {}

    def load_all(self) -> None:
        for _, modname, _ in pkgutil.iter_modules(tools_pkg.__path__):
            if modname in {"base", "registry"}:
                continue
            mod: ModuleType = importlib.import_module(f"tools.{modname}")
            for obj in vars(mod).values():
                if hasattr(obj, "_tool"):
                    self._register(obj._tool)

    def _register(self, registered_tool) -> None:
        self._tools[registered_tool.meta.name] = registered_tool

    def get(self, name: str):
        return self._tools.get(name)

    def deregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def list_available(self) -> list:
        return [tool.meta for tool in self._tools.values()]
