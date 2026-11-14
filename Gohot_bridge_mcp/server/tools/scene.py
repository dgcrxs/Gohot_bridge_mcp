"""Scene tools: tree inspection, open/save/create."""

from typing import Any, Dict, Optional

from ..bridge import rpc
from ..registry import tool


def register(server: Any) -> None:
    """Register scene tools on *server*.

    Args:
        server: MCP server instance.
    Returns:
        None
    """
    _register_read_tools(server)
    _register_write_tools(server)


def _register_read_tools(server: Any) -> None:
    """Register scene inspection tools."""

    @tool(server, "godot_get_scene_tree", read_only=True, idempotent=True)
    async def godot_get_scene_tree(
        depth: int = -1,
        include_internal: bool = False,
        with_props: bool = False,
    ) -> Dict[str, Any]:
        """Return the node tree of the currently edited scene.

        Args:
            depth: Recursion limit, -1 means unlimited.
            include_internal: Include editor-only nodes whose name starts with `@`.
            with_props: Attach serialized properties to every node (larger payload).
        Returns:
            dict: `{"root": {"name","type","path","script","children":[...]}, "count": int}`.
        """
        return await rpc(
            "scene.get_tree",
            {"depth": int(depth), "include_internal": bool(include_internal), "with_props": bool(with_props)},
        )

    @tool(server, "godot_get_current_scene", read_only=True, idempotent=True)
    async def godot_get_current_scene() -> Dict[str, Any]:
        """Describe the scene that is currently open in the editor.

        Args:
            None.
        Returns:
            dict: `{"path": str, "modified": bool, "root_name": str, "root_type": str}`.
        """
        return await rpc("scene.current")

    @tool(server, "godot_list_open_scenes", read_only=True, idempotent=True)
    async def godot_list_open_scenes() -> Dict[str, Any]:
        """List all scenes open in the editor, marking the active one.

        Args:
            None.
        Returns:
            dict: `{"scenes": [{"path": str, "current": bool, "modified": bool}]}`.
        """
        return await rpc("scene.list_open")


def _register_write_tools(server: Any) -> None:
    """Register scene open / save / create tools."""

    @tool(server, "godot_open_scene", read_only=False)
    async def godot_open_scene(path: str) -> Dict[str, Any]:
        """Open a scene file in the editor.

        Args:
            path: Scene resource path, e.g. `res://scenes/main.tscn`.
        Returns:
            dict: `{"path": str, "root_type": str}`.
        """
        return await rpc("scene.open", {"path": path})

    @tool(server, "godot_save_scene", read_only=False, idempotent=True)
    async def godot_save_scene(path: Optional[str] = None) -> Dict[str, Any]:
        """Save the current scene, optionally to a different path.

        Args:
            path: Target path; omit to save in place (untitled scenes must pass one).
        Returns:
            dict: `{"saved": bool, "path": str}`.
        """
        params: Dict[str, Any] = {}
        if path:
            params["path"] = path
        return await rpc("scene.save", params)

    @tool(server, "godot_save_scene_as", read_only=False)
    async def godot_save_scene_as(path: str) -> Dict[str, Any]:
        """Save the current scene under a new path (Save As).

        Args:
            path: New scene path, e.g. `res://scenes/level_02.tscn`.
        Returns:
            dict: `{"saved": bool, "path": str}`.
        """
        return await rpc("scene.save_as", {"path": path})

    @tool(server, "godot_create_scene", read_only=False)
    async def godot_create_scene(path: str, root_type: str = "Node2D", root_name: str = "Root") -> Dict[str, Any]:
        """Create a new scene file with a single root node and open it.

        Args:
            path: Target scene path, e.g. `res://scenes/level_01.tscn`.
            root_type: Root node class, e.g. `Node2D`, `Node3D`, `Control`.
            root_name: Name of the root node.
        Returns:
            dict: `{"path": str, "root_type": str}`.
        """
        return await rpc("scene.create", {"path": path, "root_type": root_type, "root_name": root_name})


__all__ = ["register"]
