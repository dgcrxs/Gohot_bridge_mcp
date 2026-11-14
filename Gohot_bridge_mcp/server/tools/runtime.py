"""Runtime tools: run/stop the game, logs, screenshots, input injection, live eval."""

from typing import Any, Dict, List

from ..bridge import rpc
from ..registry import tool


def register(server: Any) -> None:
    """Register runtime tools on *server*.

    Args:
        server: MCP server instance.
    Returns:
        None
    """
    _register_control_tools(server)
    _register_observation_tools(server)
    _register_interaction_tools(server)


def _register_control_tools(server: Any) -> None:
    """Register game process control tools."""

    @tool(server, "godot_run_scene", read_only=False)
    async def godot_run_scene(scene: str = "") -> Dict[str, Any]:
        """Start the game (Play) in the running game process.

        Args:
            scene: Scene to run; empty runs the scene currently open in the editor.
        Returns:
            dict: `{"running": bool, "scene": str}`.
        """
        params: Dict[str, Any] = {}
        if scene:
            params["scene"] = scene
        return await rpc("runtime.run", params, 60.0)

    @tool(server, "godot_stop_scene", read_only=False)
    async def godot_stop_scene() -> Dict[str, Any]:
        """Stop the running game.

        Args:
            None.
        Returns:
            dict: `{"running": false}`.
        """
        return await rpc("runtime.stop")

    @tool(server, "godot_get_runtime_status", read_only=True, idempotent=True)
    async def godot_get_runtime_status() -> Dict[str, Any]:
        """Report whether the game is running and which scene it plays.

        Args:
            None.
        Returns:
            dict: `{"running": bool, "connected": bool, "scene": str, "pid": int?}`.
        """
        return await rpc("runtime.status")


def _register_observation_tools(server: Any) -> None:
    """Register log, screenshot and tree observation tools."""

    @tool(server, "godot_get_game_logs", read_only=True, idempotent=True)
    async def godot_get_game_logs(since: int = 0, level: str = "", limit: int = 200) -> Dict[str, Any]:
        """Read log lines printed by the running game.

        Args:
            since: Only return entries with `seq` greater than this cursor.
            level: Filter by level: `"debug"`, `"info"`, `"warning"` or `"error"`.
            limit: Maximum number of entries.
        Returns:
            dict: `{"entries": [{"seq","ts","level","text"}], "next": int, "running": bool}`.
        """
        return await rpc("runtime.logs", {"since": int(since), "level": level, "limit": int(limit)})

    @tool(server, "godot_take_screenshot", read_only=True, idempotent=True)
    async def godot_take_screenshot(max_size: int = 1280) -> Dict[str, Any]:
        """Capture the running game viewport as a base64 PNG.

        Args:
            max_size: Longest edge of the returned image in pixels, defaults to 1280.
        Returns:
            dict: `{"png_base64": str, "width": int, "height": int}`.
        """
        return await rpc("runtime.screenshot", {"max_size": int(max_size)}, 15.0)

    @tool(server, "godot_get_runtime_tree", read_only=True, idempotent=True)
    async def godot_get_runtime_tree(depth: int = -1) -> Dict[str, Any]:
        """Return the live scene tree of the running game.

        Args:
            depth: Recursion limit, -1 means unlimited.
        Returns:
            dict: `{"root": {"name","type","path","children":[...]}}`.
        """
        return await rpc("runtime.get_tree", {"depth": int(depth)})

    @tool(server, "godot_get_runtime_node", read_only=True, idempotent=True)
    async def godot_get_runtime_node(path: str, with_props: bool = False) -> Dict[str, Any]:
        """Inspect a node inside the running game.

        Args:
            path: Absolute node path, e.g. `"/root/Main/Player"`.
            with_props: Include serialized properties.
        Returns:
            dict: `{"name": str, "type": str, "path": str, "properties"?}`.
        """
        return await rpc("runtime.get_node", {"path": path, "with_props": bool(with_props)})


def _register_interaction_tools(server: Any) -> None:
    """Register input injection and live evaluation tools."""

    @tool(server, "godot_send_input", read_only=False)
    async def godot_send_input(events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Inject synthetic input events into the running game, in order.

        Supported event shapes:
            `{"type": "key", "keycode": "SPACE", "pressed": true}`
            `{"type": "key", "keycode": "A", "pressed": true, "shift": true}`
            `{"type": "mouse", "x": 320, "y": 180, "button": "left", "pressed": true}`
            `{"type": "motion", "x": 320, "y": 180}`
            `{"type": "action", "action": "jump", "pressed": true, "strength": 1.0}`
            `{"type": "wait", "ms": 250}`

        Args:
            events: Ordered list of event dictionaries described above.
        Returns:
            dict: `{"sent": int}`.
        """
        return await rpc("runtime.input", {"events": list(events)})

    @tool(server, "godot_run_gdscript_in_game", read_only=False, destructive=True)
    async def godot_run_gdscript_in_game(code: str) -> Dict[str, Any]:
        """Evaluate a GDScript snippet inside the running game process.

        Args:
            code: GDScript statements; the snippet must `return` the value to report.
        Returns:
            dict: `{"result": any}`.
        """
        return await rpc("runtime.eval", {"code": code}, 30.0)


__all__ = ["register"]
