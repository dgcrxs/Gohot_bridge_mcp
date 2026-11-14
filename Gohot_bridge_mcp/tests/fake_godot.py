"""A reusable fake Godot editor bridge for unit tests.

The fake speaks the subset of PROTOCOL.md v2.0 that the Python transport layer
cares about (``auth``, ``ping`` and a handful of RPC methods) and can be told to
misbehave on purpose:

* ``drop_connections[method] = N`` closes the WebSocket the next *N* times that
  method is received (simulates the editor dying mid-request).
* ``silent_methods`` never answers (drives the timeout path).
* ``error_responses[method]`` answers with a protocol error envelope.
* ``noise_frames`` sends non-JSON frames before a real answer.
* ``echo`` replies out of order when a ``delay`` param is supplied, which makes
  concurrent request-id mapping observable.

Only the Python standard library plus ``websockets`` is required.
"""

import asyncio
import json
from typing import Any, Dict, List, Optional, Set

import websockets

from server.config import BridgeConfig

DEFAULT_TOKEN = "0123456789abcdef0123456789abcdef0123456789abcdef01"


def _frame(request_id: Any, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


class FakeGodot:
    """An in-process WebSocket server that pretends to be the Godot editor."""

    def __init__(
        self,
        token: str = DEFAULT_TOKEN,
        require_auth: bool = True,
        protocol: str = "2.0",
        plugin_version: str = "2.0.0",
    ) -> None:
        self.token = token
        self.require_auth = require_auth
        self.protocol = protocol
        self.plugin_version = plugin_version

        # Behaviour knobs -----------------------------------------------------
        self.drop_connections: Dict[str, int] = {}
        self.silent_methods: Set[str] = set()
        self.error_responses: Dict[str, Dict[str, Any]] = {}
        self.noise_frames: int = 0
        # Frames sent just before the next real answer (stray ids / notifications).
        self.stray_frames: List[Dict[str, Any]] = []

        # Observations --------------------------------------------------------
        self.frames: List[Dict[str, Any]] = []
        self.connection_count = 0
        self.auth_failures = 0
        self.ping_count = 0
        self.server_close_codes: List[Optional[int]] = []

        self.port = 0
        self._server: Any = None
        self._tasks: Set["asyncio.Task[Any]"] = set()

    # ------------------------------------------------------------------ setup

    async def start(self, port: int = 0) -> int:
        """Bind and start serving; returns the actual port."""
        self._server = await websockets.serve(self._handle, "127.0.0.1", port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        """Stop serving and cancel in-flight reply tasks."""
        for task in list(self._tasks):
            task.cancel()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    @property
    def uri(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    def bridge_config(self, token: Optional[str] = None, project_path: Optional[str] = None) -> BridgeConfig:
        """A BridgeConfig pointing at this fake, for :class:`GodotBridge`."""
        return BridgeConfig(
            host="127.0.0.1",
            port=self.port,
            token=self.token if token is None else token,
            project_path=project_path or "/tmp/fake-godot-project",
            handshake_path=None,
            source="fake",
        )

    # ---------------------------------------------------------------- helpers

    def methods_seen(self) -> List[str]:
        """Methods received so far, in arrival order (auth included)."""
        return [str(frame.get("method")) for frame in self.frames]

    def count(self, method: str) -> int:
        """How many frames for *method* were received."""
        return self.methods_seen().count(method)

    async def wait_for(self, predicate: Any, timeout: float = 3.0) -> None:
        """Poll *predicate* until true or raise ``AssertionError``."""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"condition not met within {timeout}s")

    # ---------------------------------------------------------------- protocol

    async def _handle(self, ws: Any) -> None:
        self.connection_count += 1
        authed = not self.require_auth
        try:
            async for raw in ws:
                try:
                    frame = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    await ws.send("this is not json")
                    continue
                if not isinstance(frame, dict):
                    await ws.send("{}")
                    continue
                self.frames.append(frame)
                method = str(frame.get("method", ""))
                request_id = frame.get("id")
                params = frame.get("params") or {}

                if method == "auth":
                    if not self.require_auth or params.get("token") == self.token:
                        await ws.send(
                            json.dumps(
                                {
                                    "jsonrpc": "2.0",
                                    "id": request_id,
                                    "result": {"ok": True, "client_id": f"c{self.connection_count}"},
                                }
                            )
                        )
                        authed = True
                    else:
                        self.auth_failures += 1
                        await ws.send(
                            json.dumps(
                                {
                                    "jsonrpc": "2.0",
                                    "id": request_id,
                                    "error": {
                                        "code": 1011,
                                        "message": "invalid token",
                                        "hint": "Read the token from .godot/mcp_bridge.json.",
                                        "context": {},
                                    },
                                }
                            )
                        )
                        await ws.close(4001)
                        self.server_close_codes.append(4001)
                        return
                    continue

                if not authed:
                    await ws.send(
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": request_id,
                                "error": {"code": 1011, "message": "auth required", "hint": "", "context": {}},
                            }
                        )
                    )
                    await ws.close(4001)
                    self.server_close_codes.append(4001)
                    return

                pending = self.drop_connections.get(method, 0)
                if pending > 0:
                    self.drop_connections[method] = pending - 1
                    await ws.close(1011)
                    return

                if method in self.silent_methods:
                    continue

                task = asyncio.ensure_future(self._reply(ws, frame))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            for task in list(self._tasks):
                task.cancel()
            self.server_close_codes.append(ws.close_code)

    async def _reply(self, ws: Any, frame: Dict[str, Any]) -> None:
        method = str(frame.get("method", ""))
        request_id = frame.get("id")
        params = frame.get("params") or {}
        if method == "echo":
            await asyncio.sleep(float(params.get("delay", 0.0)))
        if self.noise_frames > 0:
            self.noise_frames -= 1
            await ws.send("<html>not a frame</html>")
        if self.stray_frames:
            for stray in self.stray_frames:
                await ws.send(json.dumps(stray))
            self.stray_frames.clear()
        if method == "ping":
            self.ping_count += 1
        error = self.error_responses.get(method)
        if error is not None:
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": request_id, "error": error}))
            return
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": self._result(method, params)}))

    def _result(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if method == "ping":
            return {
                "pong": True,
                "protocol": self.protocol,
                "project": "FakeProject",
                "pid": 4242,
                "requires_auth": self.require_auth,
            }
        if method == "get_capabilities":
            return {
                "protocol": self.protocol,
                "plugin_version": self.plugin_version,
                "godot_version": "4.7.2",
                "methods": ["ping", "prop.set", "scene.get_tree"],
                "groups": ["system", "prop", "scene"],
            }
        if method == "get_editor_info":
            return {
                "godot_version": "4.7.2",
                "system": "Linux",
                "project_name": "FakeProject",
                "project_path": "/tmp/fake-godot-project",
                "scene_path": "res://main.tscn",
                "scene_modified": False,
                "open_scenes": [{"path": "res://main.tscn", "current": True}],
                "selection_count": 0,
                "is_playing": False,
                "locale": "en",
            }
        if method == "scene.get_tree":
            return {
                "root": {
                    "name": "Main",
                    "type": "Node2D",
                    "path": ".",
                    "script": "",
                    "owner": "",
                    "children": [
                        {
                            "name": "Player",
                            "type": "CharacterBody2D",
                            "path": "Player",
                            "script": "",
                            "owner": ".",
                            "children": [],
                        }
                    ],
                },
                "count": 2,
            }
        if method == "prop.set":
            return {"old": {"value": None}, "new": params.get("value")}
        if method == "echo":
            return {"params": params, "method": method}
        return {"method": method, "params": params}


__all__ = ["DEFAULT_TOKEN", "FakeGodot", "_frame"]
