"""Uniform tool registration: annotations, error shaping and result normalisation.

Every MCP tool in this project is declared with :func:`tool`, which

* builds ``ToolAnnotations`` (read-only / destructive / idempotent / closed-world),
* converts exceptions into ``{"ok": false, "error": {...}}`` payloads,
* guarantees a dict result carrying ``"ok": true``.
"""

import asyncio
import functools
import inspect
import logging
from typing import Any, Callable, Dict, Optional

from . import sdkcompat
from .errors import ERROR_NAMES, TIMEOUT, GodotError, internal_error

logger = logging.getLogger("godot_bridge.registry")


def build_annotations(
    read_only: bool = False,
    destructive: bool = False,
    idempotent: bool = False,
    title: Optional[str] = None,
) -> Any:
    """Create a ToolAnnotations instance, tolerating SDK differences."""
    kwargs: Dict[str, Any] = {
        "readOnlyHint": bool(read_only),
        "destructiveHint": bool(destructive),
        "idempotentHint": bool(idempotent),
        "openWorldHint": False,
    }
    if title:
        kwargs["title"] = title
    try:
        return sdkcompat.ToolAnnotations(**kwargs)
    except TypeError:  # pragma: no cover - annotation-less SDK build
        kwargs.pop("title", None)
        return sdkcompat.ToolAnnotations(**kwargs)


def _error(code: int, message: str, hint: str = "", context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": int(code),
            "message": message,
            "hint": hint,
            "context": dict(context or {}),
        },
    }


def normalize_result(result: Any) -> Dict[str, Any]:
    """Wrap any tool return value into a dict with an ``ok`` flag."""
    if isinstance(result, dict):
        payload = dict(result)
        payload.setdefault("ok", True)
        return payload
    if isinstance(result, (list, tuple)):
        return {"ok": True, "items": list(result)}
    if result is None:
        return {"ok": True}
    return {"ok": True, "result": result}


def _wrap(func: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap *func* with error handling while preserving its signature."""

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        try:
            outcome = func(*args, **kwargs)
            if inspect.isawaitable(outcome):
                outcome = await outcome
        except GodotError as exc:
            if exc.code in ERROR_NAMES:
                logger.debug("tool %s failed with %s: %s", func.__name__, exc.name, exc.message)
            return exc.to_dict()
        except (asyncio.TimeoutError, TimeoutError):
            return _error(TIMEOUT, "operation timed out", "Retry, or pass a larger timeout.", {"tool": func.__name__})
        except Exception as exc:  # noqa: BLE001 - error boundary on purpose
            wrapped = internal_error(exc)
            logger.warning("tool %s raised %s", func.__name__, wrapped.message)
            return wrapped.to_dict()
        return normalize_result(outcome)

    try:
        wrapper.__signature__ = inspect.signature(func)  # type: ignore[attr-defined]
    except (TypeError, ValueError):  # pragma: no cover - builtins
        pass
    wrapper.__doc__ = func.__doc__
    return wrapper


def tool(
    server: Any,
    name: str,
    *,
    read_only: bool = False,
    destructive: bool = False,
    idempotent: bool = False,
    title: Optional[str] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register ``func`` as MCP tool *name* with annotations and error handling.

    Args:
        server: MCP server instance (v1 ``FastMCP`` or v2 ``MCPServer``).
        name: Public MCP tool name, e.g. ``godot_get_scene_tree``.
        read_only: True when the tool never mutates editor state.
        destructive: True when the tool can delete data.
        idempotent: True when repeating the call is harmless.
        title: Optional human readable title shown by MCP clients.
    """
    if not name or not name.strip():
        raise ValueError("tool name must be a non-empty string")

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        wrapped = _wrap(func)
        annotations = build_annotations(read_only, destructive, idempotent, title)
        register = sdkcompat.tool(
            server,
            name=name,
            title=title or name,
            description=(inspect.getdoc(func) or f"MCP tool {name}."),
            annotations=annotations,
            structured_output=False,
        )
        register(wrapped)
        # Reject unknown argument names instead of silently dropping them.
        sdkcompat.enforce_strict_arguments(server, name)
        logger.debug("registered tool %s", name)
        return wrapped

    return decorator


__all__ = ["build_annotations", "normalize_result", "tool"]
