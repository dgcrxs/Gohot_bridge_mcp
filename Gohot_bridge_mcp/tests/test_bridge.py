"""Unit tests for :mod:`server.bridge` (PROTOCOL.md §2 envelope, §6 timeouts).

Every test drives a real WebSocket against :class:`tests.fake_godot.FakeGodot`,
so the transport code is exercised end to end without a Godot process.
"""

import asyncio
import re
from typing import Any, Dict, List, Set

import pytest

from server import bridge as bridge_module
from server.bridge import (
    CONNECT_TIMEOUT,
    DEFAULT_RPC_TIMEOUT,
    WRITE_METHODS,
    GodotBridge,
    is_idempotent,
    timeout_for,
)
from server.errors import AuthError, BridgeTimeoutError, GodotError

from tests.conftest import REPO_ROOT, free_port


# --------------------------------------------------------------------------- #
# PROTOCOL.md contract helpers
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def protocol_text() -> str:
    """The protocol contract, read once."""
    return (REPO_ROOT / "PROTOCOL.md").read_text(encoding="utf-8")


def protocol_write_methods(protocol_text: str) -> Set[str]:
    """Every RPC method marked ``W`` in the PROTOCOL.md method tables."""
    section = protocol_text.split("## 3. Method groups", 1)[1].split("## 4. Value codec", 1)[0]
    found: Set[str] = set()
    for line in section.splitlines():
        match = re.match(r"^\|\s*`([A-Za-z_.]+)`\s+W\s*\|", line)
        if match:
            found.add(match.group(1))
    return found


def protocol_timeouts(protocol_text: str) -> Dict[str, float]:
    """The PROTOCOL.md §6 timeout table as ``{row label: seconds}``."""
    section = protocol_text.split("## 6. Timeouts", 1)[1].split("## 7.", 1)[0]
    table: Dict[str, float] = {}
    for line in section.splitlines():
        if not line.startswith("|") or set(line) <= set("|- "):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        match = re.search(r"(\d+(?:\.\d+)?)\s*s", cells[1])
        if match:
            table[cells[0]] = float(match.group(1))
    return table


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_auth_succeeds_with_the_shared_token(fake_godot: Any, bridge_factory: Any) -> None:
    """The first frame is ``auth`` and the bridge reports connected."""
    server = await fake_godot()
    bridge: GodotBridge = bridge_factory(server.bridge_config())

    await bridge.connect()

    assert bridge.connected is True
    assert bridge.uri == server.uri
    assert server.frames[0]["method"] == "auth"
    assert server.frames[0]["params"] == {"token": server.token}
    assert server.frames[0]["jsonrpc"] == "2.0"


@pytest.mark.asyncio
async def test_auth_failure_raises_1011(fake_godot: Any, bridge_factory: Any) -> None:
    """A rejected token must surface as AuthError/1011, not a bare exception."""
    server = await fake_godot()
    bridge: GodotBridge = bridge_factory(server.bridge_config(token="wrong-token"))

    with pytest.raises(AuthError) as excinfo:
        await bridge.connect()

    error = excinfo.value
    assert error.code == 1011
    assert "rejected" in error.message
    assert error.hint, "auth errors must tell the user where the token lives"
    assert server.auth_failures == 1
    assert bridge.connected is False
    # The editor closes with 4001 after the error frame (PROTOCOL.md §2).
    await server.wait_for(lambda: 4001 in server.server_close_codes)


@pytest.mark.asyncio
async def test_auth_is_skipped_for_an_empty_token(fake_godot: Any, bridge_factory: Any) -> None:
    """No token configured means no auth frame at all."""
    server = await fake_godot(require_auth=False)
    bridge: GodotBridge = bridge_factory(server.bridge_config(token=""))

    result = await bridge.call("ping", {})

    assert result["pong"] is True
    assert "auth" not in server.methods_seen()


# --------------------------------------------------------------------------- #
# error envelope pass-through
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_error_envelope_is_passed_through(fake_godot: Any, bridge_factory: Any) -> None:
    """code, message, hint and context survive the trip unchanged."""
    server = await fake_godot()
    server.error_responses["prop.set"] = {
        "code": 1002,
        "message": "node not found: 'Ghost'",
        "hint": "Call godot_get_scene_tree to list valid node paths.",
        "context": {"path": "Ghost"},
        "trace": ["editor_ops.gd:212"],
    }
    bridge: GodotBridge = bridge_factory(server.bridge_config())

    with pytest.raises(GodotError) as excinfo:
        await bridge.call("prop.set", {"path": "Ghost", "property": "visible", "value": True})

    error = excinfo.value
    assert error.code == 1002
    assert error.name == "node_not_found"
    assert error.message == "node not found: 'Ghost'"
    assert error.hint.startswith("Call godot_get_scene_tree")
    assert error.context == {"path": "Ghost"}
    assert error.trace == ["editor_ops.gd:212"]

    payload = error.to_dict()
    assert payload["ok"] is False
    assert payload["error"]["code"] == 1002
    assert "trace" not in payload["error"], "trace must not leak into the tool payload"


@pytest.mark.asyncio
async def test_long_trace_is_truncated_to_eight(fake_godot: Any, bridge_factory: Any) -> None:
    """PROTOCOL.md §2: at most 8 trace entries."""
    server = await fake_godot()
    server.error_responses["ping"] = {
        "code": -32603,
        "message": "boom",
        "hint": "",
        "context": {},
        "trace": [f"frame{i}" for i in range(20)],
    }
    bridge: GodotBridge = bridge_factory(server.bridge_config())

    with pytest.raises(GodotError) as excinfo:
        await bridge.call("ping", {})

    assert len(excinfo.value.trace) == 8, "PROTOCOL.md §2 caps trace at 8 entries"
    assert excinfo.value.trace[0] == "frame0"


@pytest.mark.asyncio
async def test_unknown_error_code_falls_back_gracefully(fake_godot: Any, bridge_factory: Any) -> None:
    """A non-standard code still produces a usable GodotError."""
    server = await fake_godot()
    server.error_responses["ping"] = {"code": 9999, "message": "weird"}
    bridge: GodotBridge = bridge_factory(server.bridge_config())

    with pytest.raises(GodotError) as excinfo:
        await bridge.call("ping", {})

    assert excinfo.value.code == 9999
    assert excinfo.value.name == "unknown_error"
    assert excinfo.value.hint == ""
    assert excinfo.value.context == {}


# --------------------------------------------------------------------------- #
# timeouts
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_timeout_raises_1010(fake_godot: Any, bridge_factory: Any) -> None:
    """An unanswered request becomes BridgeTimeoutError (1010)."""
    server = await fake_godot()
    server.silent_methods.add("ping")
    bridge: GodotBridge = bridge_factory(server.bridge_config())

    with pytest.raises(BridgeTimeoutError) as excinfo:
        await bridge.call("ping", {}, timeout=0.3)

    error = excinfo.value
    assert error.code == 1010
    assert "ping" in error.message
    assert "0.3s" in error.message
    assert error.context == {"method": "ping", "timeout": 0.3}


@pytest.mark.asyncio
async def test_timeout_leaves_no_pending_request(fake_godot: Any, bridge_factory: Any) -> None:
    """A timed-out request must be removed from the pending table."""
    server = await fake_godot()
    server.silent_methods.add("echo")
    bridge: GodotBridge = bridge_factory(server.bridge_config())

    with pytest.raises(BridgeTimeoutError):
        await bridge.call("echo", {"tag": "x"}, timeout=0.2)

    assert bridge._pending == {}, "pending futures leaked after a timeout"
    # The bridge is still usable afterwards.
    assert (await bridge.call("ping", {}))["pong"] is True


# --------------------------------------------------------------------------- #
# reconnect policy
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_read_call_reconnects_once(fake_godot: Any, bridge_factory: Any) -> None:
    """An idempotent read survives one transport failure by reconnecting."""
    server = await fake_godot()
    server.drop_connections["ping"] = 1
    bridge: GodotBridge = bridge_factory(server.bridge_config())

    result = await bridge.call("ping", {})

    assert result["pong"] is True
    assert server.connection_count == 2, "the bridge must reconnect exactly once"
    assert server.count("ping") == 2, "the read must be retried once"


@pytest.mark.asyncio
async def test_read_gives_up_after_one_retry(fake_godot: Any, bridge_factory: Any) -> None:
    """Two failures in a row stop the retry loop and report 1010."""
    server = await fake_godot()
    server.drop_connections["ping"] = 5
    bridge: GodotBridge = bridge_factory(server.bridge_config())

    with pytest.raises(GodotError) as excinfo:
        await bridge.call("ping", {})

    assert excinfo.value.code == 1010
    assert "lost" in excinfo.value.message.lower()
    assert excinfo.value.hint, "the error must suggest how to recover"
    assert server.count("ping") == 2, "at most two attempts for an idempotent call"
    assert server.connection_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["prop.set", "node.remove", "script.write", "runtime.eval"])
async def test_write_call_is_never_retried(fake_godot: Any, bridge_factory: Any, method: str) -> None:
    """Writes must not be replayed: that would duplicate undo entries."""
    server = await fake_godot()
    server.drop_connections[method] = 5
    bridge: GodotBridge = bridge_factory(server.bridge_config())

    with pytest.raises(GodotError) as excinfo:
        await bridge.call(method, {"path": "Player"})

    error = excinfo.value
    assert error.code == 1010
    assert "write" in error.message.lower()
    assert error.context["method"] == method
    assert server.count(method) == 1, f"{method} was replayed after a transport failure"
    assert server.connection_count == 1, "a write must not trigger a reconnect"


@pytest.mark.asyncio
async def test_connect_failure_maps_to_1010() -> None:
    """A refused connection must be reported as 1010 with a hint.

    PROTOCOL.md §5 has no "connection refused" code; the closest contract is
    1010 (timeout/transport). A raw ConnectionRefusedError escaping ``call()``
    breaks that contract and reaches the agent as an unclassified 1020.
    """
    from server.config import BridgeConfig

    config = BridgeConfig(
        host="127.0.0.1",
        port=free_port(),
        token="",
        project_path="/tmp/none",
        handshake_path=None,
        source="test",
    )
    bridge = GodotBridge(config)
    try:
        with pytest.raises(GodotError) as excinfo:
            await bridge.call("ping", {}, timeout=1.0)
        assert excinfo.value.code == 1010
        assert excinfo.value.hint
    finally:
        await bridge.close()


# --------------------------------------------------------------------------- #
# heartbeat
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_heartbeat_keeps_the_link_alive(fake_godot: Any, bridge_factory: Any, monkeypatch: Any) -> None:
    """The keepalive loop sends pings while the connection stays idle."""
    monkeypatch.setattr(bridge_module, "HEARTBEAT_INTERVAL", 0.05)
    monkeypatch.setattr(bridge_module, "HEARTBEAT_IDLE", 0.0)
    monkeypatch.setattr(bridge_module, "HEARTBEAT_TIMEOUT", 1.0)

    server = await fake_godot()
    bridge: GodotBridge = bridge_factory(server.bridge_config())

    await bridge.connect()
    await server.wait_for(lambda: server.ping_count >= 3, timeout=3.0)

    assert bridge.connected is True, "the heartbeat must not tear the link down"
    assert server.connection_count == 1


@pytest.mark.asyncio
async def test_heartbeat_survives_a_silent_ping(fake_godot: Any, bridge_factory: Any, monkeypatch: Any) -> None:
    """A ping the editor never answers must not raise out of the keepalive loop."""
    monkeypatch.setattr(bridge_module, "HEARTBEAT_INTERVAL", 0.05)
    monkeypatch.setattr(bridge_module, "HEARTBEAT_IDLE", 0.0)
    monkeypatch.setattr(bridge_module, "HEARTBEAT_TIMEOUT", 0.05)

    server = await fake_godot()
    server.silent_methods.add("ping")
    bridge: GodotBridge = bridge_factory(server.bridge_config())

    await bridge.connect()
    await asyncio.sleep(0.4)

    assert bridge._heartbeat_task is not None
    assert not bridge._heartbeat_task.done(), "heartbeat loop died on a timeout"


# --------------------------------------------------------------------------- #
# framing robustness
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_unknown_ids_and_notifications_are_ignored(fake_godot: Any, bridge_factory: Any) -> None:
    """Stray ids and notifications must not resolve the wrong future."""
    server = await fake_godot()
    server.stray_frames.append({"jsonrpc": "2.0", "id": "ghost", "result": {"ghost": True}})
    server.stray_frames.append({"jsonrpc": "2.0", "method": "progress", "params": {"pct": 10}})
    bridge: GodotBridge = bridge_factory(server.bridge_config())

    result = await bridge.call("echo", {"tag": "real"})
    assert result == {"params": {"tag": "real"}, "method": "echo"}


@pytest.mark.asyncio
async def test_non_json_frames_do_not_break_the_bridge(fake_godot: Any, bridge_factory: Any) -> None:
    """A malformed frame is logged and skipped, the next answer still lands."""
    server = await fake_godot()
    server.noise_frames = 2
    bridge: GodotBridge = bridge_factory(server.bridge_config())

    result = await bridge.call("echo", {"tag": "after-noise"})
    assert result["params"] == {"tag": "after-noise"}


@pytest.mark.asyncio
async def test_twenty_concurrent_calls_keep_their_ids(fake_godot: Any, bridge_factory: Any) -> None:
    """Concurrent requests must each receive their own answer."""
    server = await fake_godot()
    bridge: GodotBridge = bridge_factory(server.bridge_config())
    await bridge.connect()

    async def one(index: int) -> Dict[str, Any]:
        # Descending delays force the editor to answer out of order.
        delay = (20 - index) * 0.002
        return await bridge.call("echo", {"tag": f"t{index}", "delay": delay}, timeout=5.0)

    results = await asyncio.gather(*(one(index) for index in range(20)))

    tags: List[str] = [result["params"]["tag"] for result in results]
    assert tags == [f"t{index}" for index in range(20)], "responses were matched to the wrong request"
    assert len(set(tags)) == 20
    assert server.count("echo") == 20
    assert bridge._pending == {}, "pending table must be empty after the batch"


@pytest.mark.asyncio
async def test_close_releases_pending_futures(fake_godot: Any, bridge_factory: Any) -> None:
    """Closing the bridge mid-flight fails waiting callers instead of hanging."""
    server = await fake_godot()
    server.silent_methods.add("prop.set")
    bridge: GodotBridge = bridge_factory(server.bridge_config())
    await bridge.connect()

    pending = asyncio.ensure_future(bridge.call("prop.set", {"path": "Player"}, timeout=5.0))
    await server.wait_for(lambda: server.count("prop.set") == 1)

    await bridge.close()

    with pytest.raises(GodotError) as excinfo:
        await asyncio.wait_for(pending, timeout=3.0)
    assert excinfo.value.code == 1010
    assert bridge.connected is False
    assert bridge._pending == {}


# --------------------------------------------------------------------------- #
# timeout / idempotency tables derived from the contract
# --------------------------------------------------------------------------- #


def test_timeout_table_matches_protocol(protocol_text: str) -> None:
    """Every documented timeout is honoured by ``timeout_for``."""
    table = protocol_timeouts(protocol_text)
    assert table, "guard: the §6 timeout table must be parseable"

    assert CONNECT_TIMEOUT == table["connect"]
    assert DEFAULT_RPC_TIMEOUT == table["default RPC"]
    assert timeout_for("scene.save") == table["scene save / script read"]
    assert timeout_for("script.read") == table["scene save / script read"]
    assert timeout_for("execute.gdscript") == table["execute / runtime.eval"]
    assert timeout_for("runtime.eval") == table["execute / runtime.eval"]
    assert timeout_for("runtime.run") == table["runtime.run"]
    assert timeout_for("runtime.screenshot") == table["screenshot"]
    assert timeout_for("scene.get_tree") == table["default RPC"]


def test_write_methods_cover_every_protocol_w(protocol_text: str) -> None:
    """Every method marked ``W`` must be classified as non-retryable."""
    documented = protocol_write_methods(protocol_text)
    assert len(documented) >= 30, f"guard: expected many W methods, got {sorted(documented)}"

    missing = documented - WRITE_METHODS
    assert not missing, f"these write methods would be replayed after a drop: {sorted(missing)}"

    for method in documented:
        assert is_idempotent(method) is False
    assert is_idempotent("scene.get_tree") is True
    assert is_idempotent("prop.get") is True
