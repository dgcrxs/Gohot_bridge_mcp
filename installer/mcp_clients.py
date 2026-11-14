"""MCP client configuration templates and writers.

Every supported client is described by one entry in :data:`CLIENTS`. Clients
with a known, project-local configuration file are written in place (merging
with existing entries so other MCP servers survive); the rest only produce a
snippet the user can paste into their own settings file.

Only the standard library is used here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import BACKUP_SUFFIX

#: Key used for this server inside every client configuration.
SERVER_KEY = "godot"
#: Default client selected by the GUI and the CLI.
DEFAULT_CLIENT = "claude"
#: Schema OpenCode expects at the top of ``opencode.json``.
OPENCODE_SCHEMA = "https://opencode.ai/config.json"
#: Documentation hint for the Codex CLI.
CODEX_CONFIG_HINT = "~/.codex/config.toml"
#: Documentation hint for Cline.
CLINE_CONFIG_HINT = "cline_mcp_settings.json (VS Code settings)"

#: ``client key -> metadata``. ``path`` is relative to the Godot project.
CLIENTS: Dict[str, Dict[str, Any]] = {
    "claude": {
        "label": "Claude Code",
        "path": ".mcp.json",
        "container": "mcpServers",
        "writes": True,
        "hint": "<project>/.mcp.json",
    },
    "cursor": {
        "label": "Cursor",
        "path": ".cursor/mcp.json",
        "container": "mcpServers",
        "writes": True,
        "hint": "<project>/.cursor/mcp.json",
    },
    "opencode": {
        "label": "OpenCode",
        "path": "opencode.json",
        "container": "mcp",
        "writes": True,
        "hint": "<project>/opencode.json",
    },
    "codex": {
        "label": "Codex CLI",
        "path": None,
        "container": None,
        "writes": False,
        "hint": CODEX_CONFIG_HINT,
    },
    "cline": {
        "label": "Cline / VS Code",
        "path": None,
        "container": "mcpServers",
        "writes": False,
        "hint": CLINE_CONFIG_HINT,
    },
}

_CLIENT_ALIASES: Dict[str, str] = {
    "claude": "claude",
    "claude code": "claude",
    "claudecode": "claude",
    "cursor": "cursor",
    "opencode": "opencode",
    "open code": "opencode",
    "codex": "codex",
    "codex cli": "codex",
    "cline": "cline",
    "cline / vs code": "cline",
    "cline/vscode": "cline",
    "vscode": "cline",
    "vs code": "cline",
}


# --------------------------------------------------------------------- helpers


def client_keys() -> List[str]:
    """Return the supported client keys in display order."""
    return list(CLIENTS.keys())


def client_label(client: str) -> str:
    """Return the human readable name of ``client``."""
    return CLIENTS[normalize_client(client)]["label"]


def label_map() -> Dict[str, str]:
    """Return ``display name -> client key`` (used by the GUI drop-down)."""
    return {str(spec["label"]): key for key, spec in CLIENTS.items()}


def normalize_client(client: str) -> str:
    """Map a client key, display name or alias to a canonical key.

    Args:
        client: Key (``"claude"``), display name (``"Claude Code"``) or alias.
    Returns:
        The canonical client key.
    Raises:
        ValueError: If the client is not supported.
    """
    if not isinstance(client, str):
        raise ValueError("client must be a string, got %r" % (client,))
    key = _CLIENT_ALIASES.get(client.strip().lower())
    if key is None:
        raise ValueError(
            "unsupported MCP client %r (supported: %s)" % (client, ", ".join(client_keys()))
        )
    return key


def config_path(client: str, project: Path) -> Optional[Path]:
    """Return the config file path for ``client`` inside ``project`` (or None)."""
    spec = CLIENTS[normalize_client(client)]
    rel = spec.get("path")
    if not rel:
        return None
    return Path(project).expanduser() / str(rel)


def _server_entry(
    client: str,
    python_exe: str,
    server_script: str,
    python_args: Sequence[str] = (),
) -> Dict[str, Any]:
    """Build the client specific ``godot`` server entry.

    Args:
        client: Canonical client key.
        python_exe: Interpreter (or ``py`` launcher) to start.
        server_script: Absolute path of ``run_server.py``.
        python_args: Arguments inserted between the interpreter and the script
            (``["-3"]`` when only the ``py`` launcher is available).
    Returns:
        The ``godot`` entry for the client configuration file.
    """
    command = str(python_exe)
    extra = [str(item) for item in python_args]
    script = str(server_script)
    if client == "opencode":
        return {
            "type": "local",
            "command": [command, *extra, script],
            "enabled": True,
        }
    return {"command": command, "args": [*extra, script]}


def _toml_string(value: str) -> str:
    """Encode ``value`` as a TOML basic string (backslashes escaped)."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped + '"'


def build_snippet(
    client: str,
    python_exe: str,
    server_script: str,
    python_args: Sequence[str] = (),
) -> str:
    """Return a copy/paste ready configuration snippet for ``client``.

    Args:
        client: Canonical client key (or alias).
        python_exe: Absolute path of the interpreter that should run the server.
        server_script: Absolute path of ``run_server.py`` in the project.
        python_args: Arguments inserted between the interpreter and the script.
    Returns:
        A JSON or TOML snippet, ready to paste into the client settings.
    """
    key = normalize_client(client)
    py = str(python_exe)
    script = str(server_script)
    extra = [str(item) for item in python_args]
    if key == "codex":
        return "\n".join(
            [
                "# Paste into " + CODEX_CONFIG_HINT,
                "[mcp_servers." + SERVER_KEY + "]",
                "command = " + _toml_string(py),
                "args = [" + ", ".join(_toml_string(item) for item in [*extra, script]) + "]",
            ]
        )
    payload: Dict[str, Any]
    if key == "opencode":
        payload = {
            "$schema": OPENCODE_SCHEMA,
            "mcp": {SERVER_KEY: _server_entry(key, py, script, extra)},
        }
    else:
        payload = {"mcpServers": {SERVER_KEY: _server_entry(key, py, script, extra)}}
    return json.dumps(payload, indent=2, ensure_ascii=False)


# ----------------------------------------------------------------------- write


def write_client_config(
    client: str,
    project: Path,
    python_exe: str,
    *,
    server_script: str,
    python_args: Sequence[str] = (),
    note: str = "",
) -> Dict[str, Any]:
    """Write (or describe) the MCP client configuration for this project.

    Existing configuration files are merged: only the ``godot`` entry is
    replaced, every other server is preserved. The previous file content is
    backed up once as ``<name>.bak-mcp``.

    Args:
        client: Canonical client key (aliases accepted).
        project: Godot project root.
        python_exe: Interpreter of the venv (or the current interpreter).
        server_script: Absolute path of the generated ``run_server.py``.
        python_args: Arguments inserted between the interpreter and the script
            (for example ``["-3"]`` when the entry uses the ``py`` launcher).
        note: Extra note to report back (for example ``"using py launcher"``).
    Returns:
        ``{"client", "label", "path", "written", "snippet", "backup", "note"}``
        where ``path`` is ``""`` for clients the installer cannot write.
    """
    key = normalize_client(client)
    spec = CLIENTS[key]
    py = str(python_exe)
    script = str(server_script)
    notes: List[str] = [note] if note else []
    snippet = build_snippet(key, py, script, python_args)

    result: Dict[str, Any] = {
        "client": key,
        "label": str(spec["label"]),
        "path": "",
        "written": False,
        "snippet": snippet,
        "backup": "",
        "note": "",
    }

    rel = spec.get("path")
    if not spec.get("writes") or not rel:
        notes.append("manual paste required: " + str(spec["hint"]))
        result["note"] = "; ".join(notes)
        return result

    project_dir = Path(project).expanduser()
    target = project_dir / str(rel)
    target.parent.mkdir(parents=True, exist_ok=True)

    data: Dict[str, Any] = {}
    if target.is_file():
        backup = target.with_name(target.name + BACKUP_SUFFIX)
        if not backup.exists():
            backup.write_bytes(target.read_bytes())
            result["backup"] = str(backup)
        try:
            existing = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:  # pragma: no cover - unreadable config is rare
            raise OSError("cannot read %s: %s" % (target, exc)) from exc
        if existing.strip():
            try:
                loaded = json.loads(existing)
            except ValueError:
                loaded = None
                notes.append("existing file was not valid JSON, it was replaced")
            if isinstance(loaded, dict):
                data = loaded

    container = str(spec["container"])
    servers = data.get(container)
    if not isinstance(servers, dict):
        servers = {}
    servers[SERVER_KEY] = _server_entry(key, py, script, python_args)
    data[container] = servers
    if key == "opencode":
        data.setdefault("$schema", OPENCODE_SCHEMA)

    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    target.write_text(text, encoding="utf-8", newline="\n")
    result["path"] = str(target)
    result["written"] = True
    result["note"] = "; ".join(notes)
    return result


__all__ = [
    "CLIENTS",
    "DEFAULT_CLIENT",
    "SERVER_KEY",
    "OPENCODE_SCHEMA",
    "build_snippet",
    "client_keys",
    "client_label",
    "config_path",
    "label_map",
    "normalize_client",
    "write_client_config",
]
