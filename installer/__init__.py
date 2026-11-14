"""Godot Bridge MCP graphical installer.

A zero-dependency (standard library only) installer that takes a beginner from
"downloaded an exe" to "my MCP client can drive Godot" in one click:

1. copies ``addons/godot_mcp`` into the Godot project,
2. enables the plugin inside ``project.godot``,
3. creates an isolated virtualenv with the Python dependencies,
4. writes the configuration for the selected MCP client.

The package is split so that the logic stays testable without a display:

``installer_core``  orchestration + public API (no tkinter import)
``sources``         locating the addon / server sources (repo or payload)
``project_file``    line based ``project.godot`` editing
``server_runtime``  deploying the server package and ``run_server.py``
``mcp_clients``     client config templates and file writers
``venv_setup``      virtualenv creation and pip installation
``gui``             tkinter front-end (imported lazily by ``__main__``)
"""

from __future__ import annotations

APP_NAME = "Godot Bridge MCP"
VERSION = "2.0.0"
PROTOCOL_VERSION = "2.0"

#: Repository directory that holds the addon and the python server package.
REPO_DIR_NAME = "Gohot_bridge_mcp"
#: Path of the editor plugin relative to the repository root *and* to a project.
ADDON_RELPATH = "addons/godot_mcp"
#: Directory name of the plugin inside ``addons/``.
ADDON_DIR_NAME = "godot_mcp"
#: Python package with the MCP server implementation.
SERVER_PACKAGE = "server"
#: Directory created inside the Godot project for the server runtime.
RUNTIME_DIR_REL = ".godot-bridge-mcp"
#: Launcher generated next to the copied ``server`` package.
RUN_SERVER_NAME = "run_server.py"
#: PyInstaller payload folder (see ``installer/build.spec``).
PAYLOAD_DIR = "_payload"
#: Suffix used for every backup created by the installer.
BACKUP_SUFFIX = ".bak-mcp"

#: Environment overrides, mostly useful for tests and packaging experiments.
ENV_ADDON_SOURCE = "GODOT_BRIDGE_ADDON_SOURCE"
ENV_SERVER_SOURCE = "GODOT_BRIDGE_SERVER_SOURCE"

#: ``res://`` path of the plugin descriptor written into ``project.godot``.
PLUGIN_RES_PATH = "res://" + ADDON_RELPATH + "/plugin.cfg"

__all__ = [
    "APP_NAME",
    "VERSION",
    "PROTOCOL_VERSION",
    "REPO_DIR_NAME",
    "ADDON_RELPATH",
    "ADDON_DIR_NAME",
    "SERVER_PACKAGE",
    "RUNTIME_DIR_REL",
    "RUN_SERVER_NAME",
    "PAYLOAD_DIR",
    "BACKUP_SUFFIX",
    "ENV_ADDON_SOURCE",
    "ENV_SERVER_SOURCE",
    "PLUGIN_RES_PATH",
]
