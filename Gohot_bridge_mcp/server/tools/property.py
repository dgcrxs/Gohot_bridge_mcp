"""Property tools: list, read, write and reset node properties."""

from typing import Any, Dict

from ..bridge import rpc
from ..registry import tool


def register(server: Any) -> None:
    """Register property tools on *server*.

    Args:
        server: MCP server instance.
    Returns:
        None
    """
    _register_read_tools(server)
    _register_write_tools(server)


def _register_read_tools(server: Any) -> None:
    """Register property inspection tools."""

    @tool(server, "godot_list_properties", read_only=True, idempotent=True)
    async def godot_list_properties(path: str, with_values: bool = False) -> Dict[str, Any]:
        """List every inspector property of a node with its type and hint.

        Args:
            path: Node path, e.g. `"Player/Sprite2D"`.
            with_values: Also return the current serialized value of each property.
        Returns:
            dict: `{"properties": [{"name","type","hint","usage","value"?}]}`.
        """
        return await rpc("prop.list", {"path": path, "with_values": bool(with_values)})

    @tool(server, "godot_get_property", read_only=True, idempotent=True)
    async def godot_get_property(path: str, property: str) -> Dict[str, Any]:
        """Read a single property value, encoded with the value codec.

        Args:
            path: Node path.
            property: Property name, e.g. `"modulate"` or `"position"`.
        Returns:
            dict: `{"value": any}`.
        """
        return await rpc("prop.get", {"path": path, "property": property})

    @tool(server, "godot_get_properties", read_only=True, idempotent=True)
    async def godot_get_properties(path: str, prefix: str = "") -> Dict[str, Any]:
        """Read all matching properties of a node as a flat name -> value map.

        Args:
            path: Node path.
            prefix: Only include property names starting with this text.
        Returns:
            dict: `{"properties": {name: value}}`.
        """
        return await rpc("prop.get_all", {"path": path, "prefix": prefix})


def _register_write_tools(server: Any) -> None:
    """Register property write tools."""

    @tool(server, "godot_set_property", read_only=False, idempotent=True)
    async def godot_set_property(path: str, property: str, value: Any) -> Dict[str, Any]:
        """Set one property and return the previous and new values.

        Godot types accept the permissive codec: `{"__gd":"Vector2","x":1,"y":2}`,
        `"1,2"`, `[1,2]`, `"#ff0000"`, `"red"` or plain numbers.

        Args:
            path: Node path.
            property: Property name to write.
            value: New value in any accepted encoding; `null` resets an object property.
        Returns:
            dict: `{"old": any, "new": any}`.
        """
        return await rpc("prop.set", {"path": path, "property": property, "value": value})

    @tool(server, "godot_set_properties", read_only=False, idempotent=True)
    async def godot_set_properties(path: str, values: Dict[str, Any]) -> Dict[str, Any]:
        """Set several properties in one undoable step.

        Args:
            path: Node path.
            values: Mapping of property name to new value, e.g. `{"position": [10, 20], "visible": true}`.
        Returns:
            dict: `{"applied": int, "old": {name: value}}`.
        """
        return await rpc("prop.set_many", {"path": path, "values": dict(values)})

    @tool(server, "godot_reset_property", read_only=False, idempotent=True)
    async def godot_reset_property(path: str, property: str) -> Dict[str, Any]:
        """Restore a property to the class default value.

        Args:
            path: Node path.
            property: Property name to reset.
        Returns:
            dict: `{"value": any}` with the restored value.
        """
        return await rpc("prop.reset", {"path": path, "property": property})


__all__ = ["register"]
