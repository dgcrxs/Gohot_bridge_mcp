"""Error types and error-code helpers for the Godot Bridge MCP server.

All failures that reach an MCP tool are normalised into a plain dict:

    {"ok": False, "error": {"code": int, "message": str, "hint": str, "context": {}}}
"""

from typing import Any, Dict, List, Optional

# Protocol error codes, see PROTOCOL.md section 5.
JSON_PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

NO_ACTIVE_SCENE = 1001
NODE_NOT_FOUND = 1002
PROPERTY_NOT_FOUND = 1003
CODEC_MISMATCH = 1004
PATH_OUTSIDE_PROJECT = 1005
RESOURCE_NOT_FOUND = 1006
SCRIPT_COMPILE_ERROR = 1007
RUNTIME_NOT_RUNNING = 1008
UNDO_FAILED = 1009
TIMEOUT = 1010
AUTH_FAILED = 1011
PERMISSION_DENIED = 1012
FILE_EXISTS = 1013
UNSUPPORTED = 1014

# Code used by the Python side when the bridge itself fails (never from Godot).
TRANSPORT_ERROR = 1010
BRIDGE_ERROR = 1020

ERROR_NAMES: Dict[int, str] = {
    JSON_PARSE_ERROR: "json_parse_error",
    INVALID_REQUEST: "invalid_request",
    METHOD_NOT_FOUND: "method_not_found",
    INVALID_PARAMS: "invalid_params",
    INTERNAL_ERROR: "internal_error",
    NO_ACTIVE_SCENE: "no_active_scene",
    NODE_NOT_FOUND: "node_not_found",
    PROPERTY_NOT_FOUND: "property_not_found",
    CODEC_MISMATCH: "codec_mismatch",
    PATH_OUTSIDE_PROJECT: "path_outside_project",
    RESOURCE_NOT_FOUND: "resource_not_found",
    SCRIPT_COMPILE_ERROR: "script_compile_error",
    RUNTIME_NOT_RUNNING: "runtime_not_running",
    UNDO_FAILED: "undo_failed",
    TIMEOUT: "timeout",
    AUTH_FAILED: "auth_failed",
    PERMISSION_DENIED: "permission_denied",
    FILE_EXISTS: "file_exists",
    UNSUPPORTED: "unsupported",
    BRIDGE_ERROR: "bridge_error",
}

_MAX_TRACE_ENTRIES = 8


class GodotError(Exception):
    """An error reported by the Godot editor or by the transport layer.

    Attributes:
        code: Protocol error code (see PROTOCOL.md section 5).
        message: Human readable message, always English.
        hint: Actionable suggestion for the agent, may be empty.
        context: Extra JSON-safe details (path, property, method, ...).
        trace: Up to 8 short GDScript source references.
    """

    def __init__(
        self,
        code: int,
        message: str,
        hint: str = "",
        context: Optional[Dict[str, Any]] = None,
        trace: Optional[List[str]] = None,
    ) -> None:
        self.code = int(code)
        self.message = str(message)
        self.hint = str(hint)
        self.context: Dict[str, Any] = dict(context or {})
        self.trace: List[str] = [str(item) for item in (trace or [])][:_MAX_TRACE_ENTRIES]
        super().__init__(self.message)

    @property
    def name(self) -> str:
        """Symbolic name of the error code, e.g. ``node_not_found``."""
        return ERROR_NAMES.get(self.code, "unknown_error")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to the MCP tool error payload (no traceback, no trace leak)."""
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "hint": self.hint,
                "context": self.context,
            },
        }

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"GodotError(code={self.code}, message={self.message!r})"


class AuthError(GodotError):
    """Raised when the editor rejects the handshake token."""

    def __init__(
        self,
        message: str = "authentication failed",
        hint: str = "Read the token from <project>/.godot/mcp_bridge.json or set GODOT_MCP_TOKEN.",
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(AUTH_FAILED, message, hint, context)


class ConfigError(GodotError):
    """Raised when no usable bridge configuration can be resolved."""

    def __init__(
        self,
        message: str,
        hint: str = "Start the Godot editor with the Godot Bridge plugin enabled.",
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(INTERNAL_ERROR, message, hint, context)


class BridgeTimeoutError(GodotError):
    """Raised when an RPC does not answer within its deadline."""

    def __init__(self, method: str, timeout: float) -> None:
        super().__init__(
            TIMEOUT,
            f"'{method}' timed out after {timeout:g}s",
            "The editor may be busy. Retry, or raise the timeout argument.",
            {"method": method, "timeout": timeout},
        )


def internal_error(exc: BaseException) -> GodotError:
    """Wrap an unexpected Python exception without leaking a traceback."""
    detail = str(exc).strip() or repr(exc)
    return GodotError(BRIDGE_ERROR, f"{type(exc).__name__}: {detail}")


def error_payload(exc: BaseException) -> Dict[str, Any]:
    """Return the MCP error payload for any exception."""
    if isinstance(exc, GodotError):
        return exc.to_dict()
    return internal_error(exc).to_dict()


__all__ = [
    "AuthError",
    "BRIDGE_ERROR",
    "BridgeTimeoutError",
    "ConfigError",
    "ERROR_NAMES",
    "GodotError",
    "TIMEOUT",
    "error_payload",
    "internal_error",
]
