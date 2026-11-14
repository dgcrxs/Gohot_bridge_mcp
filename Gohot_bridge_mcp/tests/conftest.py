"""Shared fixtures for the Godot Bridge MCP test suite.

Layout
------
* Unit tests (:mod:`test_config`, :mod:`test_bridge`, :mod:`test_tools`) never
  need a real Godot process; they use :class:`tests.fake_godot.FakeGodot`.
* Integration tests (:mod:`test_godot_editor`) talk to a live headless editor
  over the real WebSocket bridge. They are skipped when no editor can be found.

Environment
-----------
``GODOT_MCP_PROJECT``
    Godot project used by the integration tests (default ``/tmp/gdproj``).
``GODOT_BIN``
    Path to the Godot 4 executable (default ``/tmp/Godot_v4.7.2-stable_linux.x86_64``).
    When the binary is missing, the tests that need to *launch* Godot are skipped.
"""

import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import pytest
import pytest_asyncio

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PROJECT_DIR = Path(os.environ.get("GODOT_MCP_PROJECT", "/tmp/gdproj")).expanduser()
GODOT_BIN = Path(os.environ.get("GODOT_BIN", "/tmp/Godot_v4.7.2-stable_linux.x86_64")).expanduser()
FIXTURE_PROJECT = Path(__file__).resolve().parent / "fixtures" / "project"
HANDSHAKE_RELATIVE = Path(".godot") / "mcp_bridge.json"

# ``server.*`` reads GODOT_MCP_PROJECT at call time, but exporting it keeps the
# discovery path identical for every test that forgets to set it explicitly.
os.environ.setdefault("GODOT_MCP_PROJECT", str(PROJECT_DIR))


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #


def read_handshake(project: Path = PROJECT_DIR) -> Optional[Dict[str, Any]]:
    """Return the parsed handshake file of *project*, or ``None``."""
    path = project / HANDSHAKE_RELATIVE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def tcp_reachable(host: str, port: int, timeout: float = 0.3) -> bool:
    """True when a TCP connection to host:port succeeds."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def free_port() -> int:
    """An ephemeral port that is free right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def file_digest(path: Path) -> str:
    """SHA-256 of a file, or ``""`` when it does not exist."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def unique_name(prefix: str = "QAT") -> str:
    """A short, collision-free node name."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def editor_available(project: Path = PROJECT_DIR) -> bool:
    """True when a handshake file exists and its port answers TCP."""
    data = read_handshake(project)
    if not data:
        return False
    return tcp_reachable("127.0.0.1", int(data.get("port", 0)))


# --------------------------------------------------------------------------- #
# pytest configuration hooks
# --------------------------------------------------------------------------- #


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "integration: needs a live Godot editor")
    config.addinivalue_line("markers", "port_conflict: kills/restarts the editor, run alone")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Restore ``main.tscn`` if a test left the project on disk modified."""
    process = _GAME.get("process")
    if _GAME.get("managed") and process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            process.kill()
    for project in {PROJECT_DIR, FIXTURE_PROJECT}:
        scene = project / "main.tscn"
        pristine = session.config.cache.get("qa/main_tscn", {})
        expected = pristine.get(str(project))
        if expected and scene.exists() and file_digest(scene) != expected:
            backup = session.config.cache.get("qa/main_tscn_body", {}).get(str(project))
            if backup:
                scene.write_text(backup, encoding="utf-8")
                print(f"\n[qa] restored modified scene file: {scene}")


def pytest_collection_modifyitems(config: pytest.Config, items: List[pytest.Item]) -> None:
    """Tag the heavy tests so the default run stays fast and non-destructive."""
    for item in items:
        name = item.nodeid
        if "port_conflict" in name:
            item.add_marker(pytest.mark.port_conflict)
        if "test_godot_editor" in name:
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session", autouse=True)
def pristine_scene_snapshot(request: pytest.FixtureRequest) -> Iterator[None]:
    """Remember the on-disk scene text so the session can restore it."""
    bodies: Dict[str, str] = {}
    digests: Dict[str, str] = {}
    for project in {PROJECT_DIR, FIXTURE_PROJECT}:
        scene = project / "main.tscn"
        if scene.is_file():
            bodies[str(project)] = scene.read_text(encoding="utf-8")
            digests[str(project)] = file_digest(scene)
    request.config.cache.set("qa/main_tscn_body", bodies)
    request.config.cache.set("qa/main_tscn", digests)
    yield


# --------------------------------------------------------------------------- #
# fake editor fixtures (unit tests)
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def fake_godot() -> Any:
    """Factory fixture: ``await fake_godot()`` returns a started FakeGodot."""
    from tests.fake_godot import FakeGodot

    started: List[FakeGodot] = []

    async def make(**kwargs: Any) -> FakeGodot:
        server = FakeGodot(**kwargs)
        await server.start()
        started.append(server)
        return server

    try:
        yield make
    finally:
        for server in started:
            await server.stop()


@pytest_asyncio.fixture
async def bridge_factory() -> Any:
    """Factory fixture that builds ``GodotBridge`` instances and closes them."""
    from server.bridge import GodotBridge

    made: List[GodotBridge] = []

    def make(config: Any) -> GodotBridge:
        bridge = GodotBridge(config)
        made.append(bridge)
        return bridge

    try:
        yield make
    finally:
        for bridge in made:
            await bridge.close()


@pytest_asyncio.fixture
async def fake_env(monkeypatch: pytest.MonkeyPatch, fake_godot: Any) -> Any:
    """Point the whole server at a FakeGodot through the environment."""
    from server import config as config_module
    from server.bridge import reset_bridge

    async def install(**kwargs: Any) -> Any:
        server = await fake_godot(**kwargs)
        monkeypatch.setenv("GODOT_MCP_PORT", str(server.port))
        monkeypatch.setenv("GODOT_MCP_TOKEN", server.token if server.require_auth else "")
        monkeypatch.setenv("GODOT_MCP_PROJECT", str(FIXTURE_PROJECT))
        config_module.invalidate_cache()
        await reset_bridge()
        return server

    yield install
    config_module.invalidate_cache()
    await reset_bridge()


# --------------------------------------------------------------------------- #
# real MCP client helpers
# --------------------------------------------------------------------------- #


class McpHandle:
    """Thin convenience wrapper around ``mcp.Client`` plus cleanup bookkeeping."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.fake: Any = None
        self.temp_nodes: List[str] = []
        self.temp_files: List[str] = []
        self.temp_scripts: List[str] = []

    # -- calling ----------------------------------------------------------

    async def call(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Call an MCP tool and return its parsed JSON payload."""
        result = await self.client.call_tool(name, arguments or {})
        text = "".join(getattr(chunk, "text", "") or "" for chunk in (result.content or []))
        try:
            payload = json.loads(text)
        except ValueError:
            payload = {"raw": text}
        if not isinstance(payload, dict):
            payload = {"result": payload}
        payload["_sdk_error"] = bool(getattr(result, "is_error", False))
        return payload

    async def ok(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Call a tool, asserting the envelope says ``ok: true``."""
        payload = await self.call(name, arguments)
        assert payload.get("ok") is True, f"{name} failed: {payload}"
        return payload

    async def failure(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Call a tool, asserting the envelope says ``ok: false``; returns error dict."""
        payload = await self.call(name, arguments)
        assert payload.get("ok") is False, f"{name} was expected to fail: {payload}"
        error = payload.get("error")
        assert isinstance(error, dict), f"{name} returned no error object: {payload}"
        return error

    async def rpc(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Call one RPC method directly (bypassing the MCP tool layer)."""
        from server.bridge import rpc as bridge_rpc

        return await bridge_rpc(method, params or {})

    async def rpc_error(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Call an RPC method expecting a :class:`GodotError`; returns the error."""
        from server.errors import GodotError

        try:
            await self.rpc(method, params)
        except GodotError as exc:
            return exc
        raise AssertionError(f"{method} was expected to fail but succeeded")

    # -- fixtures ---------------------------------------------------------

    async def new_node(self, type: str = "Node2D", parent: str = "", name: Optional[str] = None) -> str:
        """Create a uniquely named node and register it for cleanup."""
        node_name = name or unique_name(type.replace("2D", "").replace("3D", ""))
        payload = await self.ok("godot_add_node", {"parent_path": parent, "type": type, "name": node_name})
        path = payload["path"]
        self.temp_nodes.append(path)
        return path

    async def new_script(self, source: str = "extends Node\n", suffix: str = ".gd") -> str:
        """Create a throw-away script file and register it for cleanup."""
        path = f"res://{unique_name('qa_script')}{suffix}"
        await self.ok("godot_create_script", {"path": path, "source": source})
        self.temp_scripts.append(path)
        return path

    async def new_file(self, content: str = "qa\n") -> str:
        """Create a throw-away text file and register it for cleanup."""
        path = f"res://{unique_name('qa_file')}.txt"
        await self.ok("godot_write_file", {"path": path, "content": content})
        self.temp_files.append(path)
        return path

    async def cleanup(self) -> None:
        """Delete everything this handle created; never raises."""
        for path in reversed(self.temp_nodes):
            await self._quiet("godot_delete_node", {"path": path})
        for path in reversed(self.temp_scripts):
            await self._quiet("godot_delete_script", {"path": path})
        for path in reversed(self.temp_files):
            await self._quiet("godot_delete_resource", {"path": path})

    async def _quiet(self, name: str, arguments: Dict[str, Any]) -> None:
        try:
            await self.call(name, arguments)
        except Exception:  # noqa: BLE001 - cleanup must never fail a test
            pass


async def _run_session(holder: List["McpHandle"], ready: asyncio.Event, stop: asyncio.Event, reset: bool) -> None:
    """Own an ``mcp.Client`` session for its whole lifetime inside one task.

    ``mcp.Client`` is built on anyio cancel scopes, which cannot be exited from
    a different task than the one that entered them. Keeping enter *and* exit in
    this task makes the fixture teardown safe under pytest-asyncio.
    """
    from mcp import Client
    from server.bridge import reset_bridge
    from server.server import create_server

    if reset:
        await reset_bridge()
    client = Client(create_server())
    async with client:
        holder.append(McpHandle(client))
        ready.set()
        await stop.wait()
    if reset:
        await reset_bridge()


@contextlib.asynccontextmanager
async def mcp_session(reset: bool = False) -> Any:
    """Open an in-process MCP client session for use inside a test or fixture."""
    holder: List[McpHandle] = []
    ready = asyncio.Event()
    stop = asyncio.Event()
    task = asyncio.ensure_future(_run_session(holder, ready, stop, reset=reset))
    await asyncio.wait_for(ready.wait(), timeout=15)
    try:
        yield holder[0]
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=15)


@pytest_asyncio.fixture
async def editor() -> Any:
    """MCP client bound to the live Godot editor (skips when unavailable)."""
    if not editor_available():
        pytest.skip(f"no live Godot editor for {PROJECT_DIR} (missing or unreachable handshake)")

    async with mcp_session(reset=True) as handle:
        try:
            yield handle
        finally:
            await handle.cleanup()


@pytest_asyncio.fixture
async def mcp_fake(fake_env: Any) -> Any:
    """MCP client whose tools talk to a FakeGodot (exposed as ``handle.fake``)."""
    server = await fake_env()
    async with mcp_session() as handle:
        handle.fake = server
        yield handle


# --------------------------------------------------------------------------- #
# game process fixture (runtime bridge)
# --------------------------------------------------------------------------- #


_GAME: Dict[str, Any] = {"process": None, "managed": False}


def _spawn_game() -> Optional[subprocess.Popen]:
    """Launch a headless game process for the runtime tests."""
    if not GODOT_BIN.is_file() or not (PROJECT_DIR / "project.godot").is_file():
        return None
    log = open("/tmp/gdproj/qa-game-pytest.log", "wb")
    return subprocess.Popen(
        [str(GODOT_BIN), "--headless", "--path", str(PROJECT_DIR)],
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


@pytest_asyncio.fixture
async def game_process(editor: Any) -> Any:
    """Ensure the runtime bridge of a game process is connected.

    Reuses an already connected game, otherwise launches one (only once per
    session) and waits for ``runtime.status.connected``. The process is killed
    in ``pytest_sessionfinish`` when this suite started it.
    """
    status = await editor.call("godot_get_runtime_status")
    if status.get("connected") is True:
        yield None
        return

    process = _GAME["process"]
    if process is None or process.poll() is not None:
        process = _spawn_game()
        _GAME["process"] = process
        _GAME["managed"] = True
    if process is None:
        pytest.skip("cannot launch a game process for the runtime tests")

    for _ in range(150):
        status = await editor.call("godot_get_runtime_status")
        if status.get("connected") is True:
            break
        await asyncio.sleep(0.2)
    else:  # pragma: no cover - slow machine
        pytest.skip("the game process did not connect to the runtime bridge")
    yield process


@pytest.fixture(scope="session")
def godot_bin() -> Path:
    """Path to the Godot executable used for launch-based tests."""
    if not GODOT_BIN.is_file():
        pytest.skip(f"GODOT_BIN not found at {GODOT_BIN}")
    return GODOT_BIN


@pytest.fixture(scope="session")
def fixture_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A throw-away copy of ``tests/fixtures/project`` with the addon installed."""
    target = tmp_path_factory.mktemp("gdproj") / "project"
    shutil.copytree(FIXTURE_PROJECT, target)
    addon_src = REPO_ROOT / "addons" / "godot_mcp"
    if addon_src.is_dir():
        shutil.copytree(addon_src, target / "addons" / "godot_mcp", dirs_exist_ok=True)
    return target
