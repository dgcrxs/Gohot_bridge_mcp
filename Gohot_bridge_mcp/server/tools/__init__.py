"""MCP tool modules for the Godot Bridge server.

Each submodule exposes ``register(server)`` which attaches its tools to the MCP
server instance.
"""

from typing import Any, List

from . import (
    docs,
    legacy,
    node,
    property as property_module,
    resource,
    runtime,
    scene,
    script,
    signal as signal_module,
    system,
)

MODULES: List[Any] = [
    system,
    scene,
    node,
    property_module,
    signal_module,
    script,
    resource,
    runtime,
    docs,
    legacy,
]


def register_all(server: Any) -> int:
    """Register every tool module on *server* and return the module count.

    Args:
        server: MCP server instance (v1 ``FastMCP`` or v2 ``MCPServer``).
    Returns:
        int: number of tool modules registered.
    """
    for module in MODULES:
        module.register(server)
    return len(MODULES)


__all__ = ["MODULES", "register_all"]
