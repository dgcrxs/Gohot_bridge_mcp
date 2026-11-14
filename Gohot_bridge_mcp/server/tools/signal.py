"""Signal tools: list, connect and disconnect node signals (PROTOCOL.md §3.5)."""

from typing import Any, Dict, List, Optional

from ..bridge import rpc
from ..registry import tool


def register(server: Any) -> None:
    """Register signal tools on *server*.

    Args:
        server: MCP server instance.
    Returns:
        None
    """
    _register_read_tools(server)
    _register_write_tools(server)


def _register_read_tools(server: Any) -> None:
    """Register the signal inspection tool."""

    @tool(server, "godot_list_signals", read_only=True, idempotent=True)
    async def godot_list_signals(path: str, include_internal: bool = False) -> Dict[str, Any]:
        """List every signal of a node with its arguments and live connections.

        Args:
            path: Node path, e.g. `"Player"`.
            include_internal: Also report editor-internal connections (targets or
                methods containing `"@"`), which are filtered out by default.
        Returns:
            dict: `{"signals": [{"name", "args": [{"name","type"}], "connections": [{"target","method","flags"}]}]}`.
        """
        return await rpc("signal.list", {"path": path, "include_internal": bool(include_internal)})


def _register_write_tools(server: Any) -> None:
    """Register the signal connect / disconnect tools."""

    @tool(server, "godot_connect_signal", read_only=False, idempotent=True)
    async def godot_connect_signal(
        path: str,
        signal: str,
        target_path: str,
        method: str,
        flags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Connect a node signal to a method on another node in one undo step.

        Args:
            path: Node that emits the signal, e.g. `"Player"`.
            signal: Signal name, e.g. `"body_entered"`.
            target_path: Node that owns the handler method.
            method: Handler method name on the target node.
            flags: Optional connect flags: `"one_shot"`, `"deferred"`, `"persist"`,
                `"reference_counted"`.
        Returns:
            dict: `{"connected": bool}`.
        """
        params: Dict[str, Any] = {
            "path": path,
            "signal": signal,
            "target_path": target_path,
            "method": method,
        }
        if flags:
            params["flags"] = list(flags)
        return await rpc("signal.connect", params)

    @tool(server, "godot_disconnect_signal", read_only=False, idempotent=True)
    async def godot_disconnect_signal(path: str, signal: str, target_path: str, method: str) -> Dict[str, Any]:
        """Remove one signal connection in one undo step.

        Args:
            path: Node that emits the signal.
            signal: Signal name, e.g. `"body_entered"`.
            target_path: Node that owns the handler method.
            method: Handler method name on the target node.
        Returns:
            dict: `{"disconnected": bool}`.
        """
        return await rpc(
            "signal.disconnect",
            {"path": path, "signal": signal, "target_path": target_path, "method": method},
        )


__all__ = ["register"]
