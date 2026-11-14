"""Script tools: read, write, create, delete, attach, detach, check, list."""

from typing import Any, Dict

from ..bridge import rpc
from ..registry import tool

_TEMPLATE = 'extends {base}\n\n\nfunc _ready() -> void:\n\tpass\n'


def register(server: Any) -> None:
    """Register script tools on *server*.

    Args:
        server: MCP server instance.
    Returns:
        None
    """
    _register_file_tools(server)
    _register_binding_tools(server)


def _register_file_tools(server: Any) -> None:
    """Register tools that read, write, create, delete and check script files."""

    @tool(server, "godot_read_script", read_only=True, idempotent=True)
    async def godot_read_script(path: str) -> Dict[str, Any]:
        """Read a `.gd` script file as UTF-8 text.

        Args:
            path: Script resource path, e.g. `res://scripts/player.gd`.
        Returns:
            dict: `{"path": str, "source": str, "lines": int}`.
        """
        return await rpc("script.read", {"path": path})

    @tool(server, "godot_write_script", read_only=False, idempotent=True)
    async def godot_write_script(path: str, source: str) -> Dict[str, Any]:
        """Overwrite a script file, preserving its newline style.

        Args:
            path: Script resource path (must already exist).
            source: Full script text; pass the complete file, not a diff.
        Returns:
            dict: `{"path": str, "bytes": int}`.
        """
        return await rpc("script.write", {"path": path, "source": source})

    @tool(server, "godot_create_script", read_only=False)
    async def godot_create_script(path: str, source: str = "", extends: str = "Node") -> Dict[str, Any]:
        """Create a new `.gd` file, optionally with generated boilerplate.

        Args:
            path: Target script path, e.g. `res://scripts/enemy.gd`.
            source: Full script text; when empty a `_ready()` skeleton is generated.
            extends: Base class used for the generated skeleton.
        Returns:
            dict: `{"path": str}`.
        """
        body = source if source.strip() else _TEMPLATE.format(base=extends or "Node")
        return await rpc("script.create", {"path": path, "source": body, "extends": extends})

    @tool(server, "godot_delete_script", read_only=False, destructive=True)
    async def godot_delete_script(path: str) -> Dict[str, Any]:
        """Delete a script file from the project.

        Args:
            path: Script resource path to delete.
        Returns:
            dict: `{"deleted": bool, "path": str}`.
        """
        return await rpc("script.delete", {"path": path})


def _register_binding_tools(server: Any) -> None:
    """Register tools that attach, detach, check and list scripts."""

    @tool(server, "godot_attach_script", read_only=False)
    async def godot_attach_script(node_path: str, script_path: str) -> Dict[str, Any]:
        """Attach a script to a node in the edited scene.

        Args:
            node_path: Target node path.
            script_path: Script resource path to attach.
        Returns:
            dict: `{"attached": bool, "path": str}`.
        """
        return await rpc("script.attach", {"node_path": node_path, "script_path": script_path})

    @tool(server, "godot_detach_script", read_only=False)
    async def godot_detach_script(node_path: str) -> Dict[str, Any]:
        """Remove the script from a node.

        Args:
            node_path: Node path whose script should be detached.
        Returns:
            dict: `{"detached": bool}`.
        """
        return await rpc("script.detach", {"node_path": node_path})

    @tool(server, "godot_check_script", read_only=True, idempotent=True)
    async def godot_check_script(source: str = "", path: str = "") -> Dict[str, Any]:
        """Compile-check GDScript source or an existing script file.

        Args:
            source: Inline source to check; ignored when `path` is given.
            path: Existing script path to check instead of `source`.
        Returns:
            dict: `{"ok": bool, "errors": [{"line": int, "message": str}]}`.
        """
        params: Dict[str, Any] = {}
        if path:
            params["path"] = path
        elif source:
            params["source"] = source
        return await rpc("script.check", params)

    @tool(server, "godot_list_scripts", read_only=True, idempotent=True)
    async def godot_list_scripts(dir: str = "res://", recursive: bool = True) -> Dict[str, Any]:
        """List `.gd` files in the project with their class name and base class.

        Args:
            dir: Directory to scan, defaults to the project root `res://`.
            recursive: Recurse into subdirectories.
        Returns:
            dict: `{"scripts": [{"path","class_name","extends"}]}`.
        """
        return await rpc("script.list", {"dir": dir, "recursive": bool(recursive)})


__all__ = ["register"]
