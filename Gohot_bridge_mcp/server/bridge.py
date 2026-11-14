"""WebSocket bridge to the running Godot editor.

One :class:`GodotBridge` instance owns one connection: it authenticates, maps
request ids to futures, keeps the link alive with a periodic ``ping`` and
reconnects once for idempotent calls.
"""

import asyncio
import json
import logging
from typing import Any, Dict, Optional, Tuple

import websockets
from websockets.exceptions import ConnectionClosed

from .config import BridgeConfig, resolve_config
from .errors import AuthError, BridgeTimeoutError, GodotError

logger = logging.getLogger("godot_bridge.bridge")

CONNECT_TIMEOUT = 3.0
AUTH_TIMEOUT = 5.0
DEFAULT_RPC_TIMEOUT = 10.0
MAX_FRAME_SIZE = 32 * 1024 * 1024
HEARTBEAT_INTERVAL = 5.0
HEARTBEAT_IDLE = 10.0
HEARTBEAT_TIMEOUT = 8.0

DEFAULT_TIMEOUTS: Dict[str, float] = {
    "scene.save": 20.0,
    "script.read": 20.0,
    "execute.gdscript": 30.0,
    "runtime.eval": 30.0,
    "runtime.run": 60.0,
    "runtime.screenshot": 15.0,
    "runtime.logs": 10.0,
}

# Methods that mutate editor state: never retried after a transport failure.
WRITE_METHODS = frozenset(
    {
        "set_config",
        "log.clear",
        "scene.open",
        "scene.save",
        "scene.save_as",
        "scene.create",
        "node.add",
        "node.remove",
        "node.rename",
        "node.move",
        "node.duplicate",
        "node.call",
        "prop.set",
        "prop.set_many",
        "prop.reset",
        "signal.connect",
        "signal.disconnect",
        "script.write",
        "script.create",
        "script.delete",
        "script.attach",
        "script.detach",
        "res.create",
        "res.duplicate",
        "res.delete",
        "res.reimport",
        "file.write",
        "editor.set_selection",
        "editor.undo",
        "editor.redo",
        "editor.inspect",
        "editor.open_script",
        "editor.set_project_setting",
        "runtime.run",
        "runtime.stop",
        "runtime.eval",
        "execute.gdscript",
    }
)


def timeout_for(method: str) -> float:
    """Default timeout for an RPC method (PROTOCOL.md section 6)."""
    return DEFAULT_TIMEOUTS.get(method, DEFAULT_RPC_TIMEOUT)


def is_idempotent(method: str) -> bool:
    """True when the call can be replayed safely after a reconnect."""
    return method not in WRITE_METHODS


class GodotBridge:
    """A lazily connected, self-healing WebSocket client for the editor."""

    def __init__(self, config: BridgeConfig) -> None:
        self.config = config
        self._ws: Any = None
        self._pending: Dict[str, Tuple[int, "asyncio.Future[Any]"]] = {}
        self._counter = 0
        self._generation = 0
        self._connected = False
        self._last_send = 0.0
        self._lock = asyncio.Lock()
        self._recv_task: Optional["asyncio.Task[Any]"] = None
        self._heartbeat_task: Optional["asyncio.Task[Any]"] = None

    # ------------------------------------------------------------------ state

    @property
    def connected(self) -> bool:
        """True while a socket is open and authenticated."""
        return self._connected and self._ws is not None

    @property
    def uri(self) -> str:
        return self.config.uri

    # ------------------------------------------------------------- connection

    async def connect(self) -> None:
        """Open the socket (and authenticate) unless already connected."""
        async with self._lock:
            await self._connect_locked()

    async def _connect_locked(self) -> None:
        if self.connected:
            return
        logger.debug("connecting to %s", self.uri)
        socket = await websockets.connect(
            self.uri,
            open_timeout=CONNECT_TIMEOUT,
            ping_interval=20,
            ping_timeout=20,
            max_size=MAX_FRAME_SIZE,
        )
        self._ws = socket
        self._generation += 1
        generation = self._generation
        self._connected = True
        self._recv_task = asyncio.ensure_future(self._receive_loop(socket, generation))
        try:
            if self.config.requires_auth:
                await self._authenticate()
        except BaseException:
            await self._teardown_locked()
            raise
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.ensure_future(self._heartbeat_loop())
        logger.debug("connected to %s (generation %s)", self.uri, generation)

    async def close(self) -> None:
        """Close the socket and cancel background tasks."""
        heartbeat = self._heartbeat_task
        self._heartbeat_task = None
        if heartbeat is not None:
            heartbeat.cancel()
        async with self._lock:
            await self._teardown_locked()

    async def _teardown_locked(self) -> None:
        socket = self._ws
        self._ws = None
        self._connected = False
        receiver = self._recv_task
        if receiver is not None and not receiver.done():
            receiver.cancel()
        if socket is not None:
            try:
                await socket.close()
            except (OSError, ConnectionClosed) as exc:  # pragma: no cover - best effort
                logger.debug("error while closing socket: %s", exc)
        self._fail_pending(self._generation, ConnectionError("bridge connection closed"))

    async def _recover(self) -> None:
        """Drop a dead socket and connect again."""
        async with self._lock:
            await self._teardown_locked()
            await self._connect_locked()

    async def _authenticate(self) -> None:
        payload = self._envelope("0", "auth", {"token": self.config.token})
        future: "asyncio.Future[Any]" = asyncio.get_running_loop().create_future()
        self._pending["0"] = (self._generation, future)
        try:
            await self._write(payload)
            result = await asyncio.wait_for(future, AUTH_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise AuthError("authentication timed out") from exc
        except GodotError as exc:
            raise AuthError(
                f"the editor rejected the bridge token: {exc.message}",
                exc.hint or "Copy the token from <project>/.godot/mcp_bridge.json or set GODOT_MCP_TOKEN.",
                {"uri": self.uri, "editor_code": exc.code},
            ) from exc
        finally:
            self._pending.pop("0", None)
        if not isinstance(result, dict) or not result.get("ok"):
            raise AuthError(
                "the editor rejected the bridge token",
                "Copy the token from <project>/.godot/mcp_bridge.json or set GODOT_MCP_TOKEN.",
                {"uri": self.uri},
            )

    # -------------------------------------------------------------- transport

    def _envelope(self, request_id: str, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}

    async def _write(self, payload: Dict[str, Any]) -> None:
        if self._ws is None:
            raise ConnectionError("bridge is not connected")
        await self._ws.send(json.dumps(payload))
        self._last_send = asyncio.get_running_loop().time()

    def _fail_pending(self, generation: int, exc: BaseException) -> None:
        for request_id, (pending_generation, future) in list(self._pending.items()):
            if pending_generation != generation or future.done():
                continue
            future.set_exception(exc)
            self._pending.pop(request_id, None)

    async def _receive_loop(self, socket: Any, generation: int) -> None:
        try:
            async for raw in socket:
                self._dispatch(raw)
        except asyncio.CancelledError:
            raise
        except (ConnectionClosed, OSError) as exc:
            logger.debug("receive loop ended: %s", exc)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("receive loop crashed: %s", exc)
        finally:
            if generation == self._generation:
                self._connected = False
                if self._ws is socket:
                    self._ws = None
                self._fail_pending(generation, ConnectionError("bridge connection closed"))

    def _dispatch(self, raw: Any) -> None:
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("ignoring non-JSON frame from editor")
            return
        if not isinstance(payload, dict):
            return
        request_id = payload.get("id")
        if request_id is None:
            logger.debug("ignoring notification %s", payload.get("method"))
            return
        entry = self._pending.pop(str(request_id), None)
        if entry is None:
            logger.debug("response for unknown request id %s", request_id)
            return
        _, future = entry
        if future.done():
            return
        error = payload.get("error")
        if isinstance(error, dict):
            future.set_exception(
                GodotError(
                    code=int(error.get("code", -32603)),
                    message=str(error.get("message", "unknown editor error")),
                    hint=str(error.get("hint", "")),
                    context=error.get("context") if isinstance(error.get("context"), dict) else {},
                    trace=error.get("trace") if isinstance(error.get("trace"), list) else [],
                )
            )
            return
        result = payload.get("result")
        future.set_result(result if isinstance(result, dict) else {"value": result})

    async def _send_request(self, method: str, params: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        loop = asyncio.get_running_loop()
        self._counter += 1
        request_id = f"mcp-{self._counter}"
        future: "asyncio.Future[Any]" = loop.create_future()
        self._pending[request_id] = (self._generation, future)
        try:
            await self._write(self._envelope(request_id, method, params))
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as exc:
            raise BridgeTimeoutError(method, timeout) from exc
        finally:
            self._pending.pop(request_id, None)

    # ------------------------------------------------------------- public API

    async def call(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """Perform one RPC call, reconnecting once for idempotent methods.

        Transport failures never escape as raw ``OSError``/``ConnectionError``:
        they are mapped onto :class:`GodotError` 1010 so every caller sees a
        protocol error with an actionable hint (PROTOCOL.md section 5).
        """
        limit = timeout_for(method) if timeout is None else float(timeout)
        payload = dict(params or {})
        attempts = 2 if is_idempotent(method) else 1
        failure: Optional[BaseException] = None
        connect_failure = False
        for attempt in range(attempts):
            try:
                await self.connect()
            except AuthError:
                raise
            except (ConnectionClosed, OSError, ConnectionError, asyncio.TimeoutError) as exc:
                failure = exc
                connect_failure = True
                self._connected = False
                logger.debug("connect failure on '%s' (attempt %s): %s", method, attempt + 1, exc)
                if attempt + 1 < attempts:
                    try:
                        await self._recover()
                    except (ConnectionClosed, OSError, ConnectionError, asyncio.TimeoutError) as recover_exc:
                        failure = recover_exc
                        logger.debug("recovery failed on '%s': %s", method, recover_exc)
                        break
                    continue
                break
            try:
                return await self._send_request(method, payload, limit)
            except (ConnectionClosed, OSError) as exc:
                failure = exc
                connect_failure = False
                self._connected = False
                logger.debug("transport failure on '%s' (attempt %s): %s", method, attempt + 1, exc)
                if attempt + 1 < attempts:
                    try:
                        await self._recover()
                    except (ConnectionClosed, OSError, ConnectionError, asyncio.TimeoutError) as recover_exc:
                        failure = recover_exc
                        connect_failure = True
                        logger.debug("recovery failed on '%s': %s", method, recover_exc)
                        break
        if connect_failure:
            raise GodotError(
                1010,
                "cannot reach the Godot editor",
                "Start Godot with the Godot Bridge MCP plugin enabled, or set GODOT_MCP_PORT.",
                {"method": method, "uri": self.uri},
            ) from failure
        if not is_idempotent(method):
            raise GodotError(
                1010,
                "connection lost during write",
                "The editor connection dropped mid-write; re-run godot_get_scene_tree to verify state.",
                {"method": method},
            ) from failure
        raise GodotError(
            1010,
            f"connection to Godot lost while calling '{method}'",
            "Make sure the Godot editor is running with the Godot Bridge plugin enabled, then retry.",
            {"method": method, "uri": self.uri},
        ) from failure

    async def _heartbeat_loop(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                if not self.connected:
                    continue
                if (loop.time() - self._last_send) < HEARTBEAT_IDLE:
                    continue
                try:
                    await self._send_request("ping", {}, HEARTBEAT_TIMEOUT)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # keepalive must never kill the loop
                    logger.debug("heartbeat ping failed: %s", exc)
                    self._connected = False
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            raise


_bridge: Optional[GodotBridge] = None
_default_config: Optional[BridgeConfig] = None


def set_default_config(config: Optional[BridgeConfig]) -> None:
    """Install a CLI-provided configuration used by all subsequent calls."""
    global _default_config
    _default_config = config


async def get_bridge(config: Optional[BridgeConfig] = None) -> GodotBridge:
    """Return the process-wide bridge, creating or replacing it as needed."""
    global _bridge
    resolved = config or _default_config or await resolve_config()
    if _bridge is not None and _bridge.config != resolved:
        await _bridge.close()
        _bridge = None
    if _bridge is None:
        _bridge = GodotBridge(resolved)
        logger.debug("created bridge for %s", resolved.describe())
    return _bridge


async def reset_bridge() -> None:
    """Close and forget the singleton bridge (tests and config changes)."""
    global _bridge
    if _bridge is not None:
        await _bridge.close()
    _bridge = None


async def rpc(
    method: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: Optional[float] = None,
) -> Any:
    """Call one RPC method through the shared bridge."""
    bridge = await get_bridge()
    return await bridge.call(method, params, timeout)


__all__ = [
    "DEFAULT_TIMEOUTS",
    "GodotBridge",
    "WRITE_METHODS",
    "get_bridge",
    "is_idempotent",
    "reset_bridge",
    "rpc",
    "set_default_config",
    "timeout_for",
]
