"""Legacy v1 tool names, kept so existing MCP client configs keep working.

The seven original tool names and their parameter signatures are preserved; each
one now forwards to a v2 RPC method and returns a dict (v1 returned JSON text).
"""

from typing import Any, Dict

from ..bridge import rpc
from ..registry import tool


def register(server: Any) -> None:
    """Register the seven v1 tools on *server*.

    Args:
        server: MCP server instance.
    Returns:
        None
    """
    _register_read_tools(server)
    _register_write_tools(server)


def _register_read_tools(server: Any) -> None:
    """Register the read-only v1 tools."""

    @tool(server, "get_scene_tree", read_only=True, idempotent=True)
    async def get_scene_tree() -> Dict[str, Any]:
        """Return the node tree of the edited scene (legacy v1 tool).

        Args:
            None.
        Returns:
            dict: `{"root": {...}, "count": int}` as produced by `scene.get_tree`.
        """
        return await rpc("scene.get_tree", {"depth": -1})

    @tool(server, "get_node_properties", read_only=True, idempotent=True)
    async def get_node_properties(node_path: str) -> Dict[str, Any]:
        """Return all properties of a node (legacy v1 tool).

        Args:
            node_path: Node path, e.g. `"Player"`.
        Returns:
            dict: `{"properties": {name: value}}`.
        """
        return await rpc("prop.get_all", {"path": node_path})

    @tool(server, "get_selected_nodes", read_only=True, idempotent=True)
    async def get_selected_nodes() -> Dict[str, Any]:
        """Return the nodes selected in the editor (legacy v1 tool).

        Args:
            None.
        Returns:
            dict: `{"nodes": [{"path","name","type"}]}`.
        """
        return await rpc("editor.get_selection")

    @tool(server, "get_editor_info", read_only=True, idempotent=True)
    async def get_editor_info() -> Dict[str, Any]:
        """Return editor metadata (legacy v1 tool).

        Args:
            None.
        Returns:
            dict: `{"godot_version","project_name","project_path","scene_path", ...}`.
        """
        return await rpc("get_editor_info")


def _register_write_tools(server: Any) -> None:
    """Register the mutating v1 tools."""

    @tool(server, "add_node", read_only=False)
    async def add_node(node_type: str, node_name: str = "", parent_path: str = "") -> Dict[str, Any]:
        """Add a node to the edited scene (legacy v1 tool).

        Args:
            node_type: Godot class name, e.g. `Sprite2D`.
            node_name: Optional node name; empty lets Godot pick one.
            parent_path: Parent node path; empty means the scene root.
        Returns:
            dict: `{"path": str, "name": str, "type": str}`.
        """
        params: Dict[str, Any] = {"type": node_type, "parent_path": parent_path or ""}
        if node_name:
            params["name"] = node_name
        return await rpc("node.add", params)

    @tool(server, "set_node_property", read_only=False, idempotent=True)
    async def set_node_property(node_path: str, property: str, value: Any) -> Dict[str, Any]:
        """Set one node property (legacy v1 tool).

        Args:
            node_path: Node path.
            property: Property name.
            value: New value in any codec-accepted form.
        Returns:
            dict: `{"old": any, "new": any}`.
        """
        return await rpc("prop.set", {"path": node_path, "property": property, "value": value})

    @tool(server, "execute_script", read_only=False, destructive=True)
    async def execute_script(code: str) -> Dict[str, Any]:
        """Run GDScript in the editor (legacy v1 tool).

        Args:
            code: GDScript statements; use `return` to report a value.
        Returns:
            dict: `{"result": any, "output": str, "elapsed_ms": int}`.
        """
        return await rpc("execute.gdscript", {"code": code})


__all__ = ["register"]
