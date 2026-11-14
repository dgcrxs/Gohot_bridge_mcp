"""Compatibility layer for the MCP Python SDK, supporting both 1.x and 2.x.

The MCP Python SDK renamed ``mcp.server.fastmcp.FastMCP`` to
``mcp.server.MCPServer`` in 2.x. Everything in this project goes through this
module so the rest of the code never imports the SDK directly.
"""

import inspect
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

_INSTALL_HINT = (
    "The 'mcp' Python SDK is missing or too old for the Godot Bridge MCP server.\n"
    "Fix it with one of:\n"
    '    pip install -U "mcp>=1.30"\n'
    '    python -m pip install -U "mcp>=1.30"\n'
    "If you are inside a virtualenv, activate it first, then restart your MCP client."
)


class _MissingAnnotations:
    """Fallback used only when ``mcp.types.ToolAnnotations`` is unavailable."""

    def __init__(
        self,
        title: Optional[str] = None,
        readOnlyHint: Optional[bool] = None,
        destructiveHint: Optional[bool] = None,
        idempotentHint: Optional[bool] = None,
        openWorldHint: Optional[bool] = None,
    ) -> None:
        self.title = title
        self.readOnlyHint = readOnlyHint
        self.destructiveHint = destructiveHint
        self.idempotentHint = idempotentHint
        self.openWorldHint = openWorldHint


def _load_server_class() -> Tuple[type, bool]:
    """Return ``(server_class, is_sdk_v2)`` or raise a helpful ImportError."""
    try:  # MCP SDK >= 2.0
        from mcp.server import MCPServer as _server_cls  # type: ignore[attr-defined]

        return _server_cls, True
    except ImportError:
        pass
    try:  # MCP SDK 1.x
        from mcp.server.fastmcp import FastMCP as _server_cls  # type: ignore[no-redef]

        return _server_cls, False
    except ImportError as exc:
        raise ImportError(_INSTALL_HINT) from exc


def _load_annotations() -> type:
    """Return a ToolAnnotations-compatible class."""
    try:
        from mcp.types import ToolAnnotations as _annotations

        return _annotations
    except ImportError:  # pragma: no cover - very old SDKs
        return _MissingAnnotations


MCPServer, SDK_V2 = _load_server_class()
ToolAnnotations = _load_annotations()

SDK_API_PATH = "mcp.server.MCPServer" if SDK_V2 else "mcp.server.fastmcp.FastMCP"
SERVER_CLASS_NAME = getattr(MCPServer, "__name__", "MCPServer")


def _accepted_kwargs(func: Callable[..., Any]) -> Optional[set]:
    """Return the keyword names *func* accepts, or None when it takes ``**kwargs``."""
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):  # pragma: no cover - builtins
        return None
    names = set()
    for name, param in signature.parameters.items():
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            return None
        if param.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            names.add(name)
    return names


def _kwarg_variants(kwargs: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Progressively smaller decorator kwargs, most capable first."""
    variants: List[Dict[str, Any]] = [dict(kwargs)]
    ordered = [
        ("structured_output", None),
        ("meta", None),
        ("icons", None),
        ("title", None),
        ("annotations", None),
        ("description", None),
    ]
    current = dict(kwargs)
    for key, _ in ordered:
        if key in current:
            current = {k: v for k, v in current.items() if k != key}
            variants.append(dict(current))
    if "name" in current:
        variants.append({"name": current["name"]})
    variants.append({})
    unique: List[Dict[str, Any]] = []
    seen = set()
    for variant in variants:
        marker = tuple(sorted(variant))
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(variant)
    return unique


def tool(server: Any, **kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator factory that registers a tool on *server*.

    Works on both SDK flavours: keyword arguments the installed SDK does not
    understand are dropped instead of raising ``TypeError``.
    """
    factory = getattr(server, "tool", None)
    if factory is None or not callable(factory):
        raise TypeError(
            f"{type(server).__name__} has no .tool() decorator; "
            f"expected an MCP SDK server object (detected API: {SDK_API_PATH})."
        )
    accepted = _accepted_kwargs(factory)
    if accepted is not None:
        kwargs = {key: value for key, value in kwargs.items() if key in accepted}
    kwargs = {key: value for key, value in kwargs.items() if value is not None}

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        last_error: Optional[TypeError] = None
        for variant in _kwarg_variants(kwargs):
            try:
                return factory(**variant)(func)
            except TypeError as exc:  # incompatible keyword set for this SDK
                last_error = exc
                continue
        raise TypeError(
            f"Unable to register tool {kwargs.get('name')!r} on {SERVER_CLASS_NAME}: {last_error}"
        )

    return decorator


def _find_registered_tool(server: Any, name: str) -> Any:
    """Best-effort lookup of the SDK's internal Tool object for *name*."""
    manager = getattr(server, "_tool_manager", None)
    if manager is None:
        return None
    getter = getattr(manager, "get_tool", None)
    if callable(getter):
        try:
            tool = getter(name)
        except Exception:  # pragma: no cover - exotic SDK build
            tool = None
        if tool is not None:
            return tool
    registry = getattr(manager, "_tools", None)
    if isinstance(registry, dict):
        return registry.get(name)
    return None


def enforce_strict_arguments(server: Any, name: str) -> bool:
    """Make the SDK reject unknown keyword arguments for tool *name*.

    The stock SDK builds its argument model without ``extra="forbid"``, so a
    typo such as ``with_prop`` (instead of ``with_props``) is silently dropped
    instead of being reported. Flipping the model config and rebuilding the
    validator restores the documented contract: unknown keys are an error, not
    a no-op.

    Args:
        server: MCP server instance the tool was registered on.
        name: Public tool name.
    Returns:
        bool: True when the tool now validates strictly (or was already).
    """
    tool = _find_registered_tool(server, name)
    if tool is None:
        return False
    metadata = getattr(tool, "fn_metadata", None)
    model = getattr(metadata, "arg_model", None)
    config = getattr(model, "model_config", None)
    if not isinstance(config, dict):  # pragma: no cover - SDK without pydantic models
        return False
    if config.get("extra") == "forbid":
        return True
    config["extra"] = "forbid"
    rebuild = getattr(model, "model_rebuild", None)
    if not callable(rebuild):  # pragma: no cover - pydantic v1
        config.pop("extra", None)
        return False
    try:
        rebuild(force=True)
    except Exception:  # pragma: no cover - incompatible model, keep the SDK default
        config.pop("extra", None)
        return False
    # Surface the constraint in the published schema too, so clients that
    # pre-validate arguments can reject typos before calling the tool.
    for attr in ("input_schema", "parameters"):
        schema = getattr(tool, attr, None)
        if isinstance(schema, dict):
            try:
                schema["additionalProperties"] = False
            except Exception:  # pragma: no cover - read-only schema mapping
                pass
    return True


def sdk_version() -> str:
    """Best-effort version string of the installed MCP SDK."""
    try:
        from importlib.metadata import version as _version

        return _version("mcp")
    except Exception:  # pragma: no cover - metadata missing
        module = sys.modules.get("mcp")
        return str(getattr(module, "__version__", "unknown"))


def websockets_version() -> str:
    """Best-effort version string of the installed ``websockets`` package."""
    try:
        from importlib.metadata import version as _version

        return _version("websockets")
    except Exception:  # pragma: no cover - metadata missing
        return "unknown"


def get_sdk_info() -> Dict[str, Any]:
    """Describe the runtime environment, used by the ``godot_server_info`` tool."""
    installed = sdk_version()
    major = 2 if SDK_V2 else 1
    return {
        "sdk": "mcp",
        "sdk_api": SDK_API_PATH,
        "sdk_major": major,
        "sdk_version": installed,
        "server_class": SERVER_CLASS_NAME,
        "compat_mode": "native-v2" if SDK_V2 else "legacy-v1",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "websockets": websockets_version(),
    }


__all__ = [
    "MCPServer",
    "SDK_API_PATH",
    "SDK_V2",
    "SERVER_CLASS_NAME",
    "ToolAnnotations",
    "enforce_strict_arguments",
    "get_sdk_info",
    "sdk_version",
    "tool",
]
