"""Documentation / class introspection tools."""

from typing import Any, Dict

from ..bridge import rpc
from ..registry import tool


def register(server: Any) -> None:
    """Register docs tools on *server*.

    Args:
        server: MCP server instance.
    Returns:
        None
    """

    @tool(server, "godot_get_class_info", read_only=True, idempotent=True)
    async def godot_get_class_info(class_name: str) -> Dict[str, Any]:
        """Describe a Godot class: base class, methods, properties, signals, enums.

        Args:
            class_name: Godot class, e.g. `CharacterBody2D` or `Node`.
        Returns:
            dict: `{"name","inherits","instantiable","methods","properties","signals","enums"}`.
        """
        return await rpc("docs.class", {"name": class_name})

    @tool(server, "godot_search_classes", read_only=True, idempotent=True)
    async def godot_search_classes(
        query: str,
        limit: int = 50,
        instantiable_only: bool = False,
    ) -> Dict[str, Any]:
        """Search Godot classes by name, to pick a valid node type.

        Args:
            query: Substring of the class name, e.g. `"Sprite"` or `"2D"`; empty lists everything.
            limit: Maximum number of classes returned.
            instantiable_only: Skip abstract classes that cannot be instantiated.
        Returns:
            dict: `{"classes": [{"name","inherits","instantiable"}], "count": int}`.
        """
        params = {"query": query, "limit": int(limit), "instantiable_only": bool(instantiable_only)}
        result = await rpc("docs.search", params)
        return result


__all__ = ["register"]
