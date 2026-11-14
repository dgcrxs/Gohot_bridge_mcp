"""Bridge discovery and configuration.

Resolution order (PROTOCOL.md section 1):
    1. explicit arguments
    2. environment variables (GODOT_MCP_PORT / GODOT_MCP_TOKEN / GODOT_MCP_PROJECT / GODOT_MCP_HANDSHAKE)
    3. ``<project>/.godot/mcp_bridge.json`` found by walking up from the cwd (max 6 levels)
    4. an unauthenticated ``ping`` scan of 127.0.0.1:8765..8784
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import websockets

from .errors import ConfigError

logger = logging.getLogger("godot_bridge.config")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
SCAN_FIRST_PORT = 8765
SCAN_LAST_PORT = 8784
HANDSHAKE_RELATIVE = os.path.join(".godot", "mcp_bridge.json")
HANDSHAKE_SCHEMA = 1
PROTOCOL_MAJOR = 2
MAX_WALK_LEVELS = 6
CACHE_TTL_SECONDS = 2.0
PROBE_TIMEOUT = 0.4

SOURCE_EXPLICIT = "explicit"
SOURCE_ENV = "env"
SOURCE_HANDSHAKE = "handshake"
SOURCE_SCAN = "scan"
SOURCE_DEFAULT = "default"


@dataclass(frozen=True)
class BridgeConfig:
    """Resolved connection settings for one Godot editor instance."""

    host: str
    port: int
    token: str
    project_path: Optional[str]
    handshake_path: Optional[str]
    source: str

    @property
    def uri(self) -> str:
        """WebSocket URI of the editor bridge."""
        return f"ws://{self.host}:{self.port}"

    @property
    def requires_auth(self) -> bool:
        """True when a token must be sent as the first frame."""
        return bool(self.token)

    def describe(self) -> str:
        """Short one-line description used for stderr logging."""
        token_state = "token=set" if self.token else "token=empty"
        project = self.project_path or "project=unknown"
        return f"{self.uri} ({self.source}, {token_state}, {project})"


_CACHE_KEY: Optional[Tuple[Any, ...]] = None
_CACHE_TIME: float = 0.0
_CACHE_VALUE: Optional[BridgeConfig] = None


def invalidate_cache() -> None:
    """Drop the cached configuration (call after changing env or project)."""
    global _CACHE_KEY, _CACHE_TIME, _CACHE_VALUE
    _CACHE_KEY = None
    _CACHE_TIME = 0.0
    _CACHE_VALUE = None


async def is_reachable(host: str, port: int, timeout: float = PROBE_TIMEOUT) -> bool:
    """Return True when a TCP connection to ``host:port`` succeeds quickly."""
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), timeout)
    except (OSError, asyncio.TimeoutError):
        pass
    return True


def _read_handshake(path: Path) -> Dict[str, Any]:
    """Load and validate a handshake file, raising ConfigError when unusable."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"cannot read handshake file {path}: {exc}",
            "Check file permissions, or delete the stale file and restart the Godot editor.",
            {"handshake_path": str(path)},
        ) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"handshake file {path} is not valid JSON: {exc}",
            "Restart the Godot editor so the plugin rewrites the handshake file.",
            {"handshake_path": str(path)},
        ) from exc
    if not isinstance(data, dict):
        raise ConfigError(
            f"handshake file {path} must contain a JSON object",
            "Restart the Godot editor so the plugin rewrites the handshake file.",
            {"handshake_path": str(path)},
        )
    _validate_handshake(data, path)
    return data


def _validate_handshake(data: Dict[str, Any], path: Path) -> None:
    """Verify schema 1 and protocol major version 2."""
    schema = data.get("schema")
    if schema != HANDSHAKE_SCHEMA:
        raise ConfigError(
            f"handshake schema {schema!r} at {path} is not supported (expected {HANDSHAKE_SCHEMA})",
            "Upgrade the Godot plugin and the Python server to the same 2.x release.",
            {"handshake_path": str(path), "schema": schema},
        )
    protocol = str(data.get("protocol", ""))
    major_text = protocol.split(".", 1)[0]
    if not major_text.isdigit() or int(major_text) != PROTOCOL_MAJOR:
        raise ConfigError(
            f"handshake protocol {protocol!r} at {path} is incompatible (expected {PROTOCOL_MAJOR}.x)",
            "Upgrade both sides: pip install -U godot-bridge-mcp and reinstall the addon from v2.0.0.",
            {"handshake_path": str(path), "protocol": protocol},
        )
    if not isinstance(data.get("port"), int):
        raise ConfigError(
            f"handshake file {path} has no integer 'port'",
            "Restart the Godot editor so the plugin rewrites the handshake file.",
            {"handshake_path": str(path)},
        )


def find_handshake(start: Optional[str] = None, levels: int = MAX_WALK_LEVELS) -> Optional[Path]:
    """Walk up from *start* (default: cwd) looking for the handshake file."""
    base = Path(start or os.getcwd()).expanduser()
    try:
        base = base.resolve()
    except OSError:  # pragma: no cover - unresolvable path
        return None
    current = base if base.is_dir() else base.parent
    for _ in range(max(1, levels)):
        candidate = current / HANDSHAKE_RELATIVE
        if candidate.is_file():
            return candidate
        if current.parent == current:
            return None
        current = current.parent
    return None


def config_from_handshake(path: Path, source: str = SOURCE_HANDSHAKE) -> BridgeConfig:
    """Build a BridgeConfig from a validated handshake file."""
    data = _read_handshake(path)
    return BridgeConfig(
        host=DEFAULT_HOST,
        port=int(data["port"]),
        token=str(data.get("token") or ""),
        project_path=(str(data["project_path"]) if data.get("project_path") else str(path.parent.parent)),
        handshake_path=str(path),
        source=source,
    )


async def probe_port(host: str, port: int, token: str = "") -> Optional[Dict[str, Any]]:
    """Send an unauthenticated ``ping`` and return the raw result when protocol 2."""
    uri = f"ws://{host}:{port}"
    try:
        async with websockets.connect(uri, open_timeout=PROBE_TIMEOUT, close_timeout=0.2) as socket:
            await socket.send(json.dumps({"jsonrpc": "2.0", "id": "probe", "method": "ping", "params": {}}))
            raw = await asyncio.wait_for(socket.recv(), timeout=PROBE_TIMEOUT + 0.2)
    except (OSError, asyncio.TimeoutError, websockets.exceptions.WebSocketException):
        return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("error"):
        return None
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    protocol = str(result.get("protocol", ""))
    major = protocol.split(".", 1)[0]
    if not major.isdigit() or int(major) != PROTOCOL_MAJOR:
        logger.debug("port %s answered with incompatible protocol %r", port, protocol)
        return None
    if result.get("requires_auth") and not token:
        logger.debug("port %s requires a token; skipping scan hit", port)
        return None
    return result


async def scan_ports(
    host: str = DEFAULT_HOST,
    token: str = "",
    first: int = SCAN_FIRST_PORT,
    last: int = SCAN_LAST_PORT,
) -> Optional[BridgeConfig]:
    """Probe ``first..last`` concurrently and return the first bridge found."""
    ports: List[int] = list(range(first, last + 1))
    results = await asyncio.gather(*(probe_port(host, port, token) for port in ports))
    for port, found in zip(ports, results):
        if found is None:
            continue
        logger.debug("discovered bridge on port %s", port)
        return BridgeConfig(
            host=host,
            port=port,
            token=token,
            project_path=(str(found["project"]) if found.get("project") else None),
            handshake_path=None,
            source=f"{SOURCE_SCAN}:{port}",
        )
    return None


def _env(name: str) -> Optional[str]:
    value = os.environ.get(name, "").strip()
    return value or None


def _env_token() -> str:
    return os.environ.get("GODOT_MCP_TOKEN", "")


def _base_config(project: Optional[str]) -> Optional[BridgeConfig]:
    """Resolve the handshake layer (explicit path, env path, or cwd walk)."""
    explicit_path = _env("GODOT_MCP_HANDSHAKE")
    if explicit_path:
        candidate = Path(explicit_path).expanduser()
        if not candidate.is_file():
            raise ConfigError(
                f"GODOT_MCP_HANDSHAKE points to a missing file: {candidate}",
                "Unset GODOT_MCP_HANDSHAKE or start the Godot editor once to write it.",
                {"handshake_path": str(candidate)},
            )
        return config_from_handshake(candidate, source=f"{SOURCE_ENV}:handshake")
    env_project = _env("GODOT_MCP_PROJECT")
    found = find_handshake(project or env_project)
    if found is not None:
        return config_from_handshake(found)
    return None


async def _resolve_uncached(
    project: Optional[str],
    port: Optional[int],
    token: Optional[str],
) -> BridgeConfig:
    """Full resolution pass, always hitting the filesystem / network."""
    sources: List[str] = []
    base = _base_config(project)
    if base is not None:
        sources.append(base.source)
    env_port = _env("GODOT_MCP_PORT")
    env_token = _env_token()

    resolved_port: Optional[int] = base.port if base else None
    resolved_token: str = base.token if base else ""
    if env_port and env_port.isdigit():
        resolved_port = int(env_port)
        sources.append(f"{SOURCE_ENV}:port")
    if env_token:
        resolved_token = env_token
        sources.append(f"{SOURCE_ENV}:token")
    if port is not None:
        resolved_port = int(port)
        sources.append(f"{SOURCE_EXPLICIT}:port")
    if token is not None:
        resolved_token = token
        sources.append(f"{SOURCE_EXPLICIT}:token")

    resolved_project = project or (base.project_path if base else None) or _env("GODOT_MCP_PROJECT")
    resolved_handshake = base.handshake_path if base else None

    if resolved_port is None:
        scanned = await scan_ports(token=resolved_token)
        if scanned is None:
            raise ConfigError(
                f"no Godot bridge found on {DEFAULT_HOST}:{SCAN_FIRST_PORT}..{SCAN_LAST_PORT}",
                "Open your Godot project and enable Project > Project Settings > Plugins > Godot Bridge.",
                {"scanned": f"{SCAN_FIRST_PORT}-{SCAN_LAST_PORT}", "handshake": HANDSHAKE_RELATIVE},
            )
        return scanned

    source = "+".join(sources) if sources else SOURCE_DEFAULT
    return BridgeConfig(
        host=DEFAULT_HOST,
        port=int(resolved_port),
        token=resolved_token,
        project_path=resolved_project,
        handshake_path=resolved_handshake,
        source=source,
    )


async def resolve_config(
    project: Optional[str] = None,
    port: Optional[int] = None,
    token: Optional[str] = None,
) -> BridgeConfig:
    """Resolve the bridge configuration, using a 2 second cache."""
    global _CACHE_KEY, _CACHE_TIME, _CACHE_VALUE
    key = (project, port, token, os.getcwd())
    now = time.monotonic()
    if _CACHE_VALUE is not None and _CACHE_KEY == key and (now - _CACHE_TIME) < CACHE_TTL_SECONDS:
        return _CACHE_VALUE
    resolved = await _resolve_uncached(project, port, token)
    _CACHE_KEY = key
    _CACHE_TIME = time.monotonic()
    _CACHE_VALUE = resolved
    logger.debug("resolved bridge config: %s", resolved.describe())
    return resolved


__all__ = [
    "BridgeConfig",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "config_from_handshake",
    "find_handshake",
    "invalidate_cache",
    "is_reachable",
    "probe_port",
    "resolve_config",
    "scan_ports",
]
