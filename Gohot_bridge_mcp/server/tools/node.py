"""Node tools: inspect, add, remove, rename, move, duplicate, find, call."""

from typing import Any, Dict, List, Optional

from ..bridge import rpc
from ..registry import tool


def register(server: Any) -> None:
    """Register node tools on *server*.

    Args:
        server: MCP server instance.
    Returns:
        None
    """
    _register_read_tools(server)
    _register_create_tools(server)
    _register_transform_tools(server)


def _register_read_tools(server: Any) -> None:
    """Register node inspection tools."""

    @tool(server, "godot_get_node", read_only=True, idempotent=True)
    async def godot_get_node(path: str, with_props: bool = False) -> Dict[str, Any]:
        """Read one node: type, owner, script, groups, child count and optional properties.

        Args:
            path: Node path; `""` or `"."` is the scene root, `"Player"` is relative to it.
            with_props: Include the serialized property dictionary.
        Returns:
            dict: `{"name","type","path","owner","script","scene_file","groups","children_count","properties"?}`.
        """
        return await rpc("node.get", {"path": path, "with_props": bool(with_props)})

    @tool(server, "godot_find_nodes", read_only=True, idempotent=True)
    async def godot_find_nodes(
        pattern: str = "",
        type: str = "",
        group: str = "",
        script: str = "",
        root_path: str = "",
        limit: int = 200,
    ) -> Dict[str, Any]:
        """Search the edited scene for nodes by name pattern, type, group or script.

        All filters are combined with AND; an empty filter matches everything.

        Args:
            pattern: Glob-ish name pattern, e.g. `"Enemy*"`.
            type: Exact or inherited Godot class name, e.g. `"Button"`.
            group: Group name the node belongs to.
            script: Script resource path the node must use.
            root_path: Subtree to search in; `""` searches the whole scene.
            limit: Maximum number of returned nodes.
        Returns:
            dict: `{"nodes": [{"path","name","type"}], "count": int}`.
        """
        params = {
            "pattern": pattern,
            "type": type,
            "group": group,
            "script": script,
            "root_path": root_path,
            "limit": int(limit),
        }
        return await rpc("node.find", params)


def _register_create_tools(server: Any) -> None:
    """Register node creation and deletion tools."""

    @tool(server, "godot_add_node", read_only=False)
    async def godot_add_node(
        parent_path: str,
        type: str,
        name: str = "",
        index: int = -1,
        properties: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Add a new node under a parent and return its path.

        Args:
            parent_path: Parent node path; `""` means the scene root.
            type: Godot class name, e.g. `Sprite2D`, `CharacterBody2D`, `Timer`.
            name: Node name; leave empty to let Godot generate one from the type.
            index: Position among siblings, -1 appends at the end.
            properties: Property values applied right after creation.
        Returns:
            dict: `{"path": str, "name": str, "type": str}`.
        """
        params: Dict[str, Any] = {"parent_path": parent_path, "type": type, "index": int(index)}
        if name:
            params["name"] = name
        if properties:
            params["properties"] = properties
        return await rpc("node.add", params)

    @tool(server, "godot_delete_node", read_only=False, destructive=True)
    async def godot_delete_node(path: str) -> Dict[str, Any]:
        """Delete a node and its children. Reversible with `godot_undo`.

        Args:
            path: Node path to remove; the scene root cannot be removed.
        Returns:
            dict: `{"removed": bool, "path": str}`.
        """
        return await rpc("node.remove", {"path": path})

    @tool(server, "godot_rename_node", read_only=False, idempotent=True)
    async def godot_rename_node(path: str, new_name: str) -> Dict[str, Any]:
        """Rename a node, keeping its position in the tree.

        Args:
            path: Current node path.
            new_name: New node name (must be a valid Godot identifier).
        Returns:
            dict: `{"path": str, "old_name": str}`.
        """
        return await rpc("node.rename", {"path": path, "name": new_name})


def _register_transform_tools(server: Any) -> None:
    """Register move, duplicate and method-call tools."""

    @tool(server, "godot_move_node", read_only=False)
    async def godot_move_node(path: str, new_parent: str, index: int = -1) -> Dict[str, Any]:
        """Re-parent a node, optionally at a specific sibling index.

        Args:
            path: Node to move.
            new_parent: Destination parent path; `""` is the scene root.
            index: Target sibling index, -1 appends.
        Returns:
            dict: `{"path": str, "parent": str}`.
        """
        return await rpc("node.move", {"path": path, "new_parent": new_parent, "index": int(index)})

    @tool(server, "godot_duplicate_node", read_only=False)
    async def godot_duplicate_node(path: str, name: str = "") -> Dict[str, Any]:
        """Duplicate a node (and children) under the same parent.

        Args:
            path: Node to copy.
            name: Name for the copy; empty keeps Godot's default suffix scheme.
        Returns:
            dict: `{"path": str}`.
        """
        params: Dict[str, Any] = {"path": path}
        if name:
            params["name"] = name
        return await rpc("node.duplicate", params)

    @tool(server, "godot_call_node_method", read_only=False, destructive=True)
    async def godot_call_node_method(path: str, method: str, args: Optional[List[Any]] = None) -> Dict[str, Any]:
        """Call a method on a node inside the editor.

        Args:
            path: Node path that owns the method.
            method: Method name, e.g. `"queue_free"` or a custom script function.
            args: Positional arguments, encoded with the value codec.
        Returns:
            dict: `{"result": any}`.
        """
        return await rpc("node.call", {"path": path, "method": method, "args": list(args or [])})


__all__ = ["register"]
