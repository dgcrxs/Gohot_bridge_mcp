"""Resource and filesystem tools: list, inspect, create, delete, search, read/write files."""

from typing import Any, Dict, Optional

from ..bridge import rpc
from ..registry import tool


def register(server: Any) -> None:
    """Register resource tools on *server*.

    Args:
        server: MCP server instance.
    Returns:
        None
    """
    _register_resource_tools(server)
    _register_mutation_tools(server)
    _register_filesystem_tools(server)


def _register_resource_tools(server: Any) -> None:
    """Register read-only resource inspection tools."""

    @tool(server, "godot_list_resources", read_only=True, idempotent=True)
    async def godot_list_resources(
        dir: str = "res://",
        type: str = "",
        recursive: bool = True,
        limit: int = 500,
    ) -> Dict[str, Any]:
        """List resource files in a project directory.

        Args:
            dir: Directory to scan, defaults to the project root.
            type: Filter by resource type, e.g. `"Texture2D"` or `"PackedScene"`.
            recursive: Recurse into subdirectories.
            limit: Maximum number of entries returned.
        Returns:
            dict: `{"entries": [{"path","type","size"}], "count": int}`.
        """
        params = {"dir": dir, "type": type, "recursive": bool(recursive), "limit": int(limit)}
        return await rpc("res.list", params)

    @tool(server, "godot_get_resource_info", read_only=True, idempotent=True)
    async def godot_get_resource_info(path: str) -> Dict[str, Any]:
        """Describe one resource: type, resource path, uid and import state.

        Args:
            path: Resource path, e.g. `res://icon.svg`.
        Returns:
            dict: `{"path": str, "type": str, "resource_path": str, "uid": str, "imported": bool}`.
        """
        return await rpc("res.info", {"path": path})

    @tool(server, "godot_load_resource", read_only=True, idempotent=True)
    async def godot_load_resource(path: str) -> Dict[str, Any]:
        """Load a resource and return its type plus serialized properties.

        Args:
            path: Resource path to load.
        Returns:
            dict: `{"type": str, "properties": {name: value}}`.
        """
        return await rpc("res.load", {"path": path})

    @tool(server, "godot_get_resource_dependencies", read_only=True, idempotent=True)
    async def godot_get_resource_dependencies(path: str) -> Dict[str, Any]:
        """List the resources a scene or resource depends on.

        Args:
            path: Resource path to inspect.
        Returns:
            dict: `{"dependencies": [{"path": str, "type": str}]}`.
        """
        return await rpc("res.dependencies", {"path": path})


def _register_mutation_tools(server: Any) -> None:
    """Register resource creation, deletion and reimport tools."""

    @tool(server, "godot_create_resource", read_only=False)
    async def godot_create_resource(
        path: str,
        type: str,
        properties: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create and save a new resource file.

        Args:
            path: Target path, e.g. `res://materials/red.tres`.
            type: Resource class name, e.g. `StandardMaterial3D`.
            properties: Property values assigned before saving.
        Returns:
            dict: `{"path": str, "type": str}`.
        """
        params: Dict[str, Any] = {"path": path, "type": type}
        if properties:
            params["properties"] = properties
        return await rpc("res.create", params)

    @tool(server, "godot_delete_resource", read_only=False, destructive=True)
    async def godot_delete_resource(path: str) -> Dict[str, Any]:
        """Delete a resource file from the project.

        Args:
            path: Resource path to delete.
        Returns:
            dict: `{"deleted": bool, "path": str}`.
        """
        return await rpc("res.delete", {"path": path})

    @tool(server, "godot_reimport_files", read_only=False, idempotent=True)
    async def godot_reimport_files(path: str = "") -> Dict[str, Any]:
        """Trigger a filesystem rescan / asset reimport.

        Args:
            path: Limit the rescan to this path; empty rescans the whole project.
        Returns:
            dict: `{"scanned": int, "files": [str]}`.
        """
        params: Dict[str, Any] = {}
        if path:
            params["path"] = path
        return await rpc("res.reimport", params)


def _register_filesystem_tools(server: Any) -> None:
    """Register raw file read/write, search and directory listing tools."""

    @tool(server, "godot_read_file", read_only=True, idempotent=True)
    async def godot_read_file(path: str, max_bytes: int = 1048576) -> Dict[str, Any]:
        """Read any project file; binary content comes back base64 encoded.

        Args:
            path: `res://` or `user://` path, or an OS path inside the project.
            max_bytes: Maximum bytes to read, defaults to 1 MiB.
        Returns:
            dict: `{"path": str, "content": str, "encoding": "utf-8"|"base64", "size": int}`.
        """
        return await rpc("file.read", {"path": path, "max_bytes": int(max_bytes)})

    @tool(server, "godot_write_file", read_only=False, idempotent=True)
    async def godot_write_file(path: str, content: str, encoding: str = "utf-8") -> Dict[str, Any]:
        """Write a project file (text, or base64 when `encoding` is `"base64"`).

        Args:
            path: `res://` or `user://` target path.
            content: File content as text, or base64 text for binary files.
            encoding: `"utf-8"` (default) or `"base64"`.
        Returns:
            dict: `{"path": str, "bytes": int}`.
        """
        return await rpc("file.write", {"path": path, "content": content, "encoding": encoding})

    @tool(server, "godot_search_files", read_only=True, idempotent=True)
    async def godot_search_files(
        query: str,
        dir: str = "res://",
        glob: str = "*.gd;*.tscn;*.tres;*.res;*.json;*.cfg;*.txt;*.md",
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Grep project files for a text query.

        Args:
            query: Text or pattern to find (case-insensitive).
            dir: Directory to search in.
            glob: Semicolon separated globs to include; matching is case-insensitive.
            limit: Maximum number of matches.
        Returns:
            dict: `{"matches": [{"path","line","text"}], "count": int}`.
        """
        params = {"query": query, "dir": dir, "glob": glob, "limit": int(limit)}
        return await rpc("fs.search", params)

    @tool(server, "godot_list_directory", read_only=True, idempotent=True)
    async def godot_list_directory(path: str = "res://", show_hidden: bool = False) -> Dict[str, Any]:
        """List directory entries without loading resources.

        Args:
            path: Directory to list, defaults to the project root.
            show_hidden: Include dot-files and hidden entries.
        Returns:
            dict: `{"entries": [{"name","path","dir","size"}]}`.
        """
        return await rpc("fs.list_dir", {"path": path, "show_hidden": bool(show_hidden)})


__all__ = ["register"]
