"""Unit tests for :mod:`server.config` (PROTOCOL.md §1 discovery rules).

No network and no Godot process is required: ports are either fake WebSocket
servers or monkeypatched resolvers.
"""

import json
import time
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

import pytest

from server import config as config_module
from server.config import (
    BridgeConfig,
    ConfigError,
    DEFAULT_HOST,
    find_handshake,
    invalidate_cache,
    probe_port,
    resolve_config,
    scan_ports,
)
from server.errors import INTERNAL_ERROR

TOKEN = "t" * 48


def write_handshake(
    project: Path,
    *,
    port: Any = 9101,
    runtime_port: Optional[int] = None,
    token: str = TOKEN,
    protocol: str = "2.0",
    schema: int = 1,
    project_name: str = "FixtureProject",
    drop: tuple = (),
) -> Path:
    """Create ``<project>/.godot/mcp_bridge.json`` and return its path."""
    directory = project / ".godot"
    directory.mkdir(parents=True, exist_ok=True)
    if runtime_port is None:
        runtime_port = port + 1 if isinstance(port, int) else 0
    payload: Dict[str, Any] = {
        "schema": schema,
        "protocol": protocol,
        "port": port,
        "runtime_port": runtime_port,
        "token": token,
        "pid": 4242,
        "project_path": str(project),
        "project_name": project_name,
        "godot_version": "4.7.2",
        "started_at": 1763100000,
    }
    for key in drop:
        payload.pop(key, None)
    path = directory / "mcp_bridge.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Isolate every test from the ambient bridge configuration."""
    for name in ("GODOT_MCP_PORT", "GODOT_MCP_TOKEN", "GODOT_MCP_HANDSHAKE", "GODOT_MCP_PROJECT"):
        monkeypatch.delenv(name, raising=False)
    invalidate_cache()
    yield
    invalidate_cache()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """An empty project directory."""
    root = tmp_path / "project"
    root.mkdir()
    return root


# --------------------------------------------------------------------------- #
# handshake parsing / validation
# --------------------------------------------------------------------------- #


def test_handshake_is_read_from_project(project: Path) -> None:
    """A valid handshake yields host/port/token/project."""
    write_handshake(project, port=9123)
    path = find_handshake(str(project))
    assert path is not None and path.is_file()

    resolved = config_module.config_from_handshake(path)
    assert (resolved.host, resolved.port, resolved.token) == (DEFAULT_HOST, 9123, TOKEN)
    assert resolved.project_path == str(project)
    assert resolved.requires_auth is True
    assert resolved.uri == f"ws://{DEFAULT_HOST}:9123"
    assert "token=set" in resolved.describe()


def test_handshake_walk_up_finds_parent(project: Path, tmp_path: Path) -> None:
    """Discovery walks up at most six levels from a nested working directory."""
    write_handshake(project)
    nested = project / "a" / "b" / "c"
    nested.mkdir(parents=True)

    found = find_handshake(str(nested))
    assert found is not None, "guard: handshake must be discovered from a nested dir"
    assert found == project / ".godot" / "mcp_bridge.json"


def test_handshake_walk_up_accepts_a_file_path(project: Path) -> None:
    """Passing a file instead of a directory still resolves the handshake."""
    write_handshake(project)
    scene = project / "main.tscn"
    scene.write_text("[gd_scene]\n", encoding="utf-8")
    assert find_handshake(str(scene)) is not None


def test_find_handshake_returns_none_without_file(tmp_path: Path) -> None:
    """No handshake anywhere means ``None``, not an exception."""
    empty = tmp_path / "nothing"
    empty.mkdir()
    assert find_handshake(str(empty), levels=1) is None


@pytest.mark.parametrize(
    "kwargs, expected_word",
    [
        ({"protocol": "1.9"}, "incompatible"),
        ({"protocol": "3.0"}, "incompatible"),
        ({"schema": 2}, "schema"),
        ({"port": "8765"}, "port"),
        ({"drop": ("port",)}, "port"),
    ],
)
def test_invalid_handshake_raises_actionable_config_error(
    project: Path, kwargs: Dict[str, Any], expected_word: str
) -> None:
    """Schema/version/shape problems raise a ConfigError with a hint."""
    path = write_handshake(project, **kwargs)
    with pytest.raises(ConfigError) as excinfo:
        config_module.config_from_handshake(path)

    error = excinfo.value
    assert error.code == INTERNAL_ERROR, "config errors surface as -32603"
    assert expected_word in error.message.lower()
    assert error.hint, "a ConfigError without a hint is not actionable"
    assert error.context.get("handshake_path") == str(path)


def test_bad_json_does_not_crash(project: Path) -> None:
    """A truncated handshake file raises ConfigError instead of JSONDecodeError."""
    directory = project / ".godot"
    directory.mkdir()
    (directory / "mcp_bridge.json").write_text('{"schema": 1, "protocol": "2.0"', encoding="utf-8")  # truncated

    with pytest.raises(ConfigError) as excinfo:
        config_module.config_from_handshake(directory / "mcp_bridge.json")
    assert "not valid json" in excinfo.value.message.lower()
    assert excinfo.value.hint


def test_non_object_handshake_is_rejected(project: Path) -> None:
    """A JSON array is not a handshake; the error must stay readable."""
    directory = project / ".godot"
    directory.mkdir()
    path = directory / "mcp_bridge.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        config_module.config_from_handshake(path)
    assert "json object" in excinfo.value.message.lower()


def test_empty_token_disables_auth(project: Path) -> None:
    """An explicitly empty token means "no auth required"."""
    path = write_handshake(project, token="")
    resolved = config_module.config_from_handshake(path)
    assert resolved.token == ""
    assert resolved.requires_auth is False


# --------------------------------------------------------------------------- #
# resolution order: explicit > env > handshake > scan
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_env_beats_handshake(monkeypatch: pytest.MonkeyPatch, project: Path) -> None:
    """GODOT_MCP_PORT / GODOT_MCP_TOKEN override the handshake file."""
    write_handshake(project, port=9101, token="handshake-token")
    monkeypatch.setenv("GODOT_MCP_PORT", "9555")
    monkeypatch.setenv("GODOT_MCP_TOKEN", "env-token")

    resolved = await resolve_config(project=str(project))
    assert resolved.port == 9555
    assert resolved.token == "env-token"
    assert resolved.source.startswith("handshake"), "handshake remains the base layer"
    assert "env:port" in resolved.source and "env:token" in resolved.source


@pytest.mark.asyncio
async def test_explicit_arguments_beat_env(monkeypatch: pytest.MonkeyPatch, project: Path) -> None:
    """Arguments passed to ``resolve_config`` win over the environment."""
    write_handshake(project, port=9101)
    monkeypatch.setenv("GODOT_MCP_PORT", "9555")
    monkeypatch.setenv("GODOT_MCP_TOKEN", "env-token")

    resolved = await resolve_config(project=str(project), port=9777, token="explicit-token")
    assert resolved.port == 9777
    assert resolved.token == "explicit-token"
    assert "explicit:port" in resolved.source and "explicit:token" in resolved.source


@pytest.mark.asyncio
async def test_env_project_is_used_for_discovery(monkeypatch: pytest.MonkeyPatch, project: Path) -> None:
    """GODOT_MCP_PROJECT tells discovery where to start walking up."""
    nested = project / "deep" / "nest"
    nested.mkdir(parents=True)
    write_handshake(project, port=9200)
    monkeypatch.setenv("GODOT_MCP_PROJECT", str(nested))

    resolved = await resolve_config()
    assert resolved.port == 9200
    assert resolved.project_path == str(project)


@pytest.mark.asyncio
async def test_handshake_env_points_to_missing_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """GODOT_MCP_HANDSHAKE with a bad path fails fast with a clear hint."""
    missing = tmp_path / "nope.json"
    monkeypatch.setenv("GODOT_MCP_HANDSHAKE", str(missing))

    with pytest.raises(ConfigError) as excinfo:
        await resolve_config()
    assert "missing file" in excinfo.value.message.lower()
    assert str(missing) in excinfo.value.message


@pytest.mark.asyncio
async def test_scan_result_is_returned_when_nothing_else_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without env or handshake the port scan provides the configuration."""
    scanned = BridgeConfig(
        host=DEFAULT_HOST, port=8770, token="scan-token", project_path="/scan", handshake_path=None, source="scan:8770"
    )

    async def fake_scan(**kwargs: Any) -> BridgeConfig:
        assert kwargs.get("token") == "scan-token", "the env token must be offered to the scan"
        return scanned

    monkeypatch.setattr(config_module, "scan_ports", fake_scan)
    monkeypatch.setenv("GODOT_MCP_TOKEN", "scan-token")
    monkeypatch.chdir(tmp_path)

    assert await resolve_config() is scanned


@pytest.mark.asyncio
async def test_scan_miss_raises_with_hint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A failed scan explains how to start the plugin."""

    async def empty_scan(**kwargs: Any) -> None:
        return None

    monkeypatch.setattr(config_module, "scan_ports", empty_scan)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ConfigError) as excinfo:
        await resolve_config()
    assert "no godot bridge found" in excinfo.value.message.lower()
    assert "plugin" in excinfo.value.hint.lower()


# --------------------------------------------------------------------------- #
# cache behaviour
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_cache_is_used_within_ttl(monkeypatch: pytest.MonkeyPatch, project: Path) -> None:
    """Two resolutions with the same key hit the cache once."""
    write_handshake(project, port=9301)
    calls = {"n": 0}
    original = config_module._resolve_uncached

    async def counting(*args: Any, **kwargs: Any) -> BridgeConfig:
        calls["n"] += 1
        return await original(*args, **kwargs)

    monkeypatch.setattr(config_module, "_resolve_uncached", counting)

    first = await resolve_config(project=str(project))
    second = await resolve_config(project=str(project))
    assert first is second
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_invalidate_cache_forces_a_new_lookup(monkeypatch: pytest.MonkeyPatch, project: Path) -> None:
    """``invalidate_cache`` is the documented way to drop the memo."""
    write_handshake(project, port=9302)
    calls = {"n": 0}
    original = config_module._resolve_uncached

    async def counting(*args: Any, **kwargs: Any) -> BridgeConfig:
        calls["n"] += 1
        return await original(*args, **kwargs)

    monkeypatch.setattr(config_module, "_resolve_uncached", counting)

    await resolve_config(project=str(project))
    invalidate_cache()
    await resolve_config(project=str(project))
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_cache_expires_after_ttl(monkeypatch: pytest.MonkeyPatch, project: Path) -> None:
    """Once the TTL elapses the handshake is read again."""
    write_handshake(project, port=9303)
    monkeypatch.setattr(config_module, "CACHE_TTL_SECONDS", 0.05)

    first = await resolve_config(project=str(project))
    assert first.port == 9303

    write_handshake(project, port=9304)
    time.sleep(0.08)
    second = await resolve_config(project=str(project))
    assert second.port == 9304, "stale config served past the TTL"


# --------------------------------------------------------------------------- #
# probing / scanning a real (fake) WebSocket endpoint
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_probe_port_accepts_protocol_2(fake_godot: Any) -> None:
    """An unauthenticated ping identifies a protocol 2.0 bridge."""
    server = await fake_godot(require_auth=False)
    found = await probe_port("127.0.0.1", server.port)
    assert found is not None
    assert found["protocol"] == "2.0"
    assert found["project"] == "FakeProject"


@pytest.mark.asyncio
async def test_probe_port_ignores_other_protocols(fake_godot: Any) -> None:
    """A v1 bridge must not be mistaken for a v2 one."""
    server = await fake_godot(require_auth=False, protocol="1.0")
    assert await probe_port("127.0.0.1", server.port) is None


@pytest.mark.asyncio
async def test_probe_port_skips_auth_requiring_bridge(fake_godot: Any) -> None:
    """Without a token a token-protected bridge is not usable, so skip it."""
    server = await fake_godot(require_auth=True)
    assert await probe_port("127.0.0.1", server.port, token="") is None


@pytest.mark.asyncio
async def test_probe_port_returns_none_for_closed_port() -> None:
    """A closed port is not an error."""
    from tests.conftest import free_port

    assert await probe_port("127.0.0.1", free_port()) is None


@pytest.mark.asyncio
async def test_scan_ports_finds_the_open_bridge(fake_godot: Any) -> None:
    """``scan_ports`` ranges over ports and reports the first hit."""
    server = await fake_godot(require_auth=False)

    found = await scan_ports(first=server.port - 1, last=server.port)
    assert found is not None
    assert found.port == server.port
    assert found.source == f"scan:{server.port}"


@pytest.mark.asyncio
async def test_is_reachable_handles_closed_port() -> None:
    """``is_reachable`` is a boolean probe, never an exception."""
    from tests.conftest import free_port

    assert await config_module.is_reachable("127.0.0.1", free_port()) is False
