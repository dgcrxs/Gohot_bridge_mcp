"""MCP server assembly, resources, prompts and CLI entry point.

Run with ``godot-bridge-mcp`` (see pyproject) or ``python -m server.server``.
stdout is reserved for the MCP protocol: all logging goes to stderr.
"""

import argparse
import asyncio
import json
import logging
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import PROTOCOL_VERSION, SERVER_NAME, __version__, sdkcompat
from .bridge import reset_bridge, rpc, set_default_config
from .config import BridgeConfig, resolve_config
from .errors import GodotError, error_payload
from .tools import register_all

logger = logging.getLogger("godot_bridge.server")

DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8000

INSTRUCTIONS = (
    "Control a running Godot 4 editor through the Godot Bridge plugin (protocol 2.0).\n"
    "Workflow: call godot_get_current_scene (or the legacy get_scene_tree) to orient yourself, "
    "then godot_find_nodes / godot_get_node to locate nodes, godot_list_properties before writing, "
    "and the godot_set_* tools to change state. Every write is a single undo step, so godot_undo "
    "reverts one tool call. Use godot_check_script before godot_write_script, and "
    "godot_read_script before editing an existing file. All tools return dicts with an 'ok' flag; "
    "on failure read error.code, error.message and error.hint (codes are documented in PROTOCOL.md). "
    "Node paths are scene relative: '' is the root, 'Player' is a child of the root. "
    "Runtime tools (godot_run_scene, godot_take_screenshot, godot_send_input, godot_get_game_logs) "
    "operate on the launched game process, not on the editor."
)


# --------------------------------------------------------------------- logging


def configure_logging(level: int = logging.INFO) -> None:
    """Send all server logs to stderr, keeping stdout clean for MCP frames."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    package_logger = logging.getLogger("godot_bridge")
    package_logger.handlers = [handler]
    package_logger.setLevel(level)
    package_logger.propagate = False


# ---------------------------------------------------------------------- server


def create_server(host: Optional[str] = None, port: Optional[int] = None) -> Any:
    """Build the MCP server with every tool, resource and prompt registered.

    Args:
        host: Optional bind host for HTTP transports (ignored by the SDKs that
            take it from their own settings object).
        port: Optional bind port for HTTP transports.
    Returns:
        MCPServer | FastMCP: ready-to-run server instance.
    """
    kwargs: Dict[str, Any] = {"instructions": INSTRUCTIONS}
    if host:
        kwargs["host"] = host
    if port:
        kwargs["port"] = port
    try:
        server = sdkcompat.MCPServer(SERVER_NAME, **kwargs)
    except TypeError:
        server = sdkcompat.MCPServer(SERVER_NAME, instructions=INSTRUCTIONS)
    count = register_all(server)
    _register_resources(server)
    _register_prompts(server)
    logger.debug("server ready: %s tool modules registered", count)
    return server


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


async def _safe_rpc(method: str, params: Optional[Dict[str, Any]] = None) -> str:
    """Call an RPC for a resource/prompt handler and always return JSON text."""
    try:
        result = await rpc(method, params)
    except GodotError as exc:
        return _json(exc.to_dict())
    except Exception as exc:  # noqa: BLE001 - resources must never crash the server
        return _json(error_payload(exc))
    return _json(result)


def _register_resource_fn(server: Any, uri: str, name: str, description: str, handler: Callable[..., Any]) -> None:
    """Register a resource handler with graceful degradation to a bare URI."""
    variants: List[Dict[str, Any]] = [
        {"name": name, "description": description, "mime_type": "application/json"},
        {"name": name, "description": description},
        {},
    ]
    for kwargs in variants:
        try:
            server.resource(uri, **kwargs)(handler)
            return
        except TypeError:
            continue
    raise TypeError(f"unable to register resource {uri} on {type(server).__name__}")


def _register_prompt_fn(server: Any, name: str, description: str, handler: Callable[..., Any]) -> None:
    """Register a prompt handler with graceful degradation to a bare name."""
    for kwargs in ({"name": name, "description": description}, {"name": name}, {}):
        try:
            server.prompt(**kwargs)(handler)
            return
        except TypeError:
            continue
    raise TypeError(f"unable to register prompt {name} on {type(server).__name__}")


def _register_resources(server: Any) -> None:
    """Attach the read-only editor resources."""

    async def editor_info() -> str:
        """Editor metadata of the connected Godot instance."""
        return await _safe_rpc("get_editor_info")

    async def scene_tree() -> str:
        """Node tree of the scene currently open in the editor."""
        return await _safe_rpc("scene.get_tree", {"depth": -1})

    async def capabilities() -> str:
        """RPC capabilities of the connected plugin."""
        return await _safe_rpc("get_capabilities")

    _register_resource_fn(server, "godot://editor/info", "godot_editor_info", "Godot editor info", editor_info)
    _register_resource_fn(server, "godot://scene/tree", "godot_scene_tree", "Edited scene tree", scene_tree)
    _register_resource_fn(
        server, "godot://editor/capabilities", "godot_capabilities", "Bridge capabilities", capabilities
    )


def _register_prompts(server: Any) -> None:
    """Attach the two guidance prompts."""

    def scaffold_2d_scene(scene_path: str, root_name: str = "Level") -> str:
        """Guided workflow to scaffold a new 2D scene."""
        return (
            f"Create a playable 2D scene at {scene_path} using Godot Bridge tools.\n"
            f"1. godot_create_scene(path='{scene_path}', root_type='Node2D', root_name='{root_name}')\n"
            "2. godot_add_node(parent_path='', type='CharacterBody2D', name='Player')\n"
            "3. godot_add_node(parent_path='Player', type='Sprite2D', name='Sprite')\n"
            "4. godot_add_node(parent_path='Player', type='CollisionShape2D', name='Collision')\n"
            "5. godot_set_properties on 'Player' with position and visible values.\n"
            "6. godot_save_scene() and then godot_get_scene_tree() to confirm the result.\n"
            "Report every godot_* call you made and any error.code you received."
        )

    def debug_running_game(symptom: str = "") -> str:
        """Guided workflow to debug the running game."""
        detail = f" Reported symptom: {symptom}." if symptom else ""
        return (
            "Debug the running game with Godot Bridge runtime tools." + detail + "\n"
            "1. godot_get_runtime_status() - is the game running?\n"
            "2. godot_get_game_logs(level='error') - read recent errors first.\n"
            "3. godot_take_screenshot() - see what the player sees.\n"
            "4. godot_get_runtime_tree() and godot_get_runtime_node(path) - inspect live state.\n"
            "5. godot_run_gdscript_in_game(code) - print the exact values you need.\n"
            "6. godot_send_input(events) - reproduce the bug with synthetic input.\n"
            "Summarise root cause, the evidence, and the smallest editor-side fix."
        )

    _register_prompt_fn(server, "scaffold_2d_scene", "Scaffold a new 2D scene step by step", scaffold_2d_scene)
    _register_prompt_fn(server, "debug_running_game", "Diagnose a bug in the running game", debug_running_game)


# ------------------------------------------------------------------------- CLI


def parse_http_target(value: str) -> Tuple[str, int]:
    """Parse ``host:port`` from ``--http``, tolerating a bare port.

    Args:
        value: Either ``host:port``, ``:port`` or ``port``.
    Returns:
        tuple[str, int]: host and port.
    """
    text = (value or "").strip()
    if not text:
        return DEFAULT_HTTP_HOST, DEFAULT_HTTP_PORT
    if text.isdigit():
        return DEFAULT_HTTP_HOST, int(text)
    host, _, port_text = text.rpartition(":")
    if not host:
        host = DEFAULT_HTTP_HOST
    try:
        port = int(port_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid --http value {value!r}, expected host:port") from exc
    return host, port


def build_parser() -> argparse.ArgumentParser:
    """Build the command line parser."""
    parser = argparse.ArgumentParser(
        prog="godot-bridge-mcp",
        description="MCP server for the Godot Bridge editor plugin (protocol 2.0).",
    )
    parser.add_argument("--port", type=int, default=None, help="Godot editor bridge port (default 8765).")
    parser.add_argument("--token", default=None, help="Bridge auth token; omit to read the handshake file.")
    parser.add_argument("--project", default=None, help="Godot project directory used to find .godot/mcp_bridge.json.")
    parser.add_argument(
        "--http",
        nargs="?",
        const=f"{DEFAULT_HTTP_HOST}:{DEFAULT_HTTP_PORT}",
        default=None,
        metavar="[host:port]",
        help="Serve streamable HTTP instead of stdio (default 127.0.0.1:8000).",
    )
    parser.add_argument("--version", action="store_true", help="Print the server version and exit.")
    return parser


async def _resolve_startup_config(args: argparse.Namespace) -> Optional[BridgeConfig]:
    """Resolve the bridge configuration at startup, never raising."""
    try:
        return await resolve_config(args.project, args.port, args.token)
    except GodotError as exc:
        logger.warning("bridge config unresolved: %s | hint: %s", exc.message, exc.hint)
        return None


def _run_server(server: Any, transport: str, host: str, port: int) -> None:
    """Run the MCP server on the requested transport."""
    if transport == "stdio":
        server.run("stdio")
        return
    try:
        server.run("streamable-http", host=host, port=port)
    except TypeError:  # SDK reads host/port from its own settings
        server.run("streamable-http")


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point used by the ``godot-bridge-mcp`` console script."""
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging()
    if args.version:
        sys.stdout.write(f"godot-bridge-mcp {__version__} (protocol {PROTOCOL_VERSION}, {sdkcompat.SDK_API_PATH})\n")
        return 0
    config = asyncio.run(_resolve_startup_config(args))
    if config is not None:
        set_default_config(config)
        logger.info("bridge config: %s", config.describe())
    else:
        logger.info("bridge config: unresolved, will retry lazily on the first tool call")
    transport = "streamable-http" if args.http else "stdio"
    host, port = parse_http_target(args.http) if args.http else (DEFAULT_HTTP_HOST, DEFAULT_HTTP_PORT)
    server = create_server(None if transport == "stdio" else host, None if transport == "stdio" else port)
    logger.info("serving %s over %s (version %s)", SERVER_NAME, transport, __version__)
    try:
        _run_server(server, transport, host, port)
    except KeyboardInterrupt:
        logger.info("server interrupted, shutting down")
    finally:
        try:
            asyncio.run(reset_bridge())
        except Exception as exc:  # noqa: BLE001 - shutdown best effort
            logger.debug("bridge shutdown raised: %s", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
