"""System / editor utility tools (``godot_`` prefix)."""

import logging
from typing import Any, Dict, List

from .. import PROTOCOL_VERSION, __version__, sdkcompat
from ..bridge import get_bridge, rpc
from ..errors import GodotError
from ..registry import tool

logger = logging.getLogger("godot_bridge.tools.system")


def register(server: Any) -> None:
    """Register system tools on *server*.

    Args:
        server: MCP server instance.
    Returns:
        None
    """
    _register_bridge_tools(server)
    _register_diagnostics_tools(server)
    _register_selection_tools(server)
    _register_undo_tools(server)
    _register_settings_tools(server)


def _register_bridge_tools(server: Any) -> None:
    """Register ping / info / capability tools."""

    @tool(server, "godot_ping", read_only=True, idempotent=True)
    async def godot_ping() -> Dict[str, Any]:
        """Ping the Godot editor bridge and measure round-trip health.

        Args:
            None.
        Returns:
            dict: `{"pong": bool, "protocol": str, "project": str, "pid": int, "requires_auth": bool}`.
        """
        return await rpc("ping")

    @tool(server, "godot_get_capabilities", read_only=True, idempotent=True)
    async def godot_get_capabilities() -> Dict[str, Any]:
        """List the RPC methods supported by the connected Godot plugin.

        Args:
            None.
        Returns:
            dict: `{"protocol": str, "plugin_version": str, "godot_version": str, "methods": [...], "groups": [...]}`.
        """
        return await rpc("get_capabilities")

    @tool(server, "godot_get_config", read_only=True, idempotent=True)
    async def godot_get_config() -> Dict[str, Any]:
        """Show editor bridge settings (port, token masked, auto start) and local config.

        Args:
            None.
        Returns:
            dict: editor config fields merged with the resolved local bridge config.
        """
        remote = await rpc("get_config")
        bridge = await get_bridge()
        payload: Dict[str, Any] = dict(remote)
        payload["local"] = {
            "uri": bridge.uri,
            "source": bridge.config.source,
            "project_path": bridge.config.project_path,
            "handshake_path": bridge.config.handshake_path,
        }
        return payload


def _register_diagnostics_tools(server: Any) -> None:
    """Register the self-diagnosing server info tool."""

    @tool(server, "godot_server_info", read_only=True, idempotent=True)
    async def godot_server_info() -> Dict[str, Any]:
        """Report MCP server version, SDK flavour, bridge target and editor info.

        Use this first when debugging: it never fails, it degrades per section.

        Args:
            None.
        Returns:
            dict: `{"server": {...}, "bridge": {...}, "editor": {...}, "capabilities": {...}}`.
        """
        bridge = await get_bridge()
        report: Dict[str, Any] = {
            "server": dict(sdkcompat.get_sdk_info(), version=__version__, protocol=PROTOCOL_VERSION),
            "bridge": {
                "uri": bridge.uri,
                "source": bridge.config.source,
                "project_path": bridge.config.project_path,
                "handshake_path": bridge.config.handshake_path,
                "connected": bridge.connected,
            },
        }
        problems: Dict[str, Any] = {}
        for key, method in (("editor", "get_editor_info"), ("capabilities", "get_capabilities")):
            try:
                report[key] = await rpc(method)
            except GodotError as exc:
                report[key] = None
                problems[key] = exc.to_dict()["error"]
        if problems:
            report["errors"] = problems
        return report


def _register_selection_tools(server: Any) -> None:
    """Register selection, script execution and RPC listing tools."""

    @tool(server, "godot_list_methods", read_only=True, idempotent=True)
    async def godot_list_methods() -> Dict[str, Any]:
        """List every RPC method name the connected plugin exposes.

        Args:
            None.
        Returns:
            dict: `{"count": int, "methods": [str], "groups": [str]}`.
        """
        capabilities = await rpc("get_capabilities")
        methods = [str(name) for name in capabilities.get("methods", [])]
        return {
            "count": len(methods),
            "methods": methods,
            "groups": [str(group) for group in capabilities.get("groups", [])],
        }

    @tool(server, "godot_execute_script", read_only=False, destructive=True)
    async def godot_execute_script(code: str) -> Dict[str, Any]:
        """Run a GDScript snippet inside the edited scene and return its value.

        The snippet body must `return` a value; it runs on a temporary node under
        the edited scene root, which is removed afterwards.

        Args:
            code: GDScript statements, e.g. `var n = get_node("../Player")\nreturn n.position`.
        Returns:
            dict: `{"result": any, "output": str, "elapsed_ms": int}`.
        """
        return await rpc("execute.gdscript", {"code": code})

    @tool(server, "godot_get_selection", read_only=True, idempotent=True)
    async def godot_get_selection() -> Dict[str, Any]:
        """Return the nodes currently selected in the editor scene dock.

        Args:
            None.
        Returns:
            dict: `{"nodes": [{"path": str, "name": str, "type": str}]}`.
        """
        return await rpc("editor.get_selection")

    @tool(server, "godot_set_selection", read_only=False)
    async def godot_set_selection(paths: List[str], inspect: bool = False) -> Dict[str, Any]:
        """Select nodes in the editor scene dock.

        Args:
            paths: Scene-relative node paths, e.g. `["Player", "Player/Sprite2D"]`.
            inspect: Also reveal the first node in the Inspector dock.
        Returns:
            dict: `{"count": int}`.
        """
        return await rpc("editor.set_selection", {"paths": list(paths), "inspect": bool(inspect)})


def _register_undo_tools(server: Any) -> None:
    """Register undo / redo tools."""

    @tool(server, "godot_undo", read_only=False)
    async def godot_undo(steps: int = 1) -> Dict[str, Any]:
        """Undo the last editor action(s).

        Args:
            steps: How many times to call undo, defaults to 1.
        Returns:
            dict: `{"done": int}`.
        """
        return await rpc("editor.undo", {"steps": int(steps)})

    @tool(server, "godot_redo", read_only=False)
    async def godot_redo(steps: int = 1) -> Dict[str, Any]:
        """Redo previously undone editor action(s).

        Args:
            steps: How many times to call redo, defaults to 1.
        Returns:
            dict: `{"done": int}`.
        """
        return await rpc("editor.redo", {"steps": int(steps)})

    @tool(server, "godot_get_undo_history", read_only=True, idempotent=True)
    async def godot_get_undo_history() -> Dict[str, Any]:
        """List the editor undo stack of the current scene, newest first.

        At most 50 actions are returned. `mergeable` is always false: UndoRedo
        does not expose the merge flag of a committed action.

        Args:
            None.
        Returns:
            dict: `{"actions": [{"name": str, "mergeable": bool}]}`.
        """
        return await rpc("editor.undo_history")


def _register_settings_tools(server: Any) -> None:
    """Register project settings tools."""

    @tool(server, "godot_get_project_settings", read_only=True, idempotent=True)
    async def godot_get_project_settings(prefix: str = "") -> Dict[str, Any]:
        """Read project settings, optionally filtered by key prefix.

        Args:
            prefix: Only return keys starting with this text, e.g. `"display/"`.
        Returns:
            dict: `{"settings": {key: value}}`.
        """
        return await rpc("editor.project_settings", {"prefix": prefix})

    @tool(server, "godot_set_project_setting", read_only=False)
    async def godot_set_project_setting(key: str, value: Any) -> Dict[str, Any]:
        """Change one project setting and save `project.godot`.

        Args:
            key: Setting name, e.g. `"application/config/name"`.
            value: New value; use `{"__gd": "Vector2", "x": 1, "y": 2}` style for Godot types.
        Returns:
            dict: `{"key": str, "old": any}`.
        """
        return await rpc("editor.set_project_setting", {"key": key, "value": value})


__all__ = ["register"]
