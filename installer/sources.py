"""Locate the plugin and server sources shipped with the installer.

Two layouts are supported:

* a repository checkout: ``<repo>/Gohot_bridge_mcp/{addons/godot_mcp,server}``
* a PyInstaller bundle: ``<_MEIPASS or exe dir>/_payload/{addons/godot_mcp,server}``

Two environment variables (``GODOT_BRIDGE_ADDON_SOURCE`` /
``GODOT_BRIDGE_SERVER_SOURCE``) override the search, which keeps tests and
custom packagings simple.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional

from . import (
    ADDON_RELPATH,
    ENV_ADDON_SOURCE,
    ENV_SERVER_SOURCE,
    PAYLOAD_DIR,
    REPO_DIR_NAME,
    SERVER_PACKAGE,
)

#: File that makes a directory a Godot editor plugin.
PLUGIN_CFG_NAME = "plugin.cfg"
#: Files and directories that are never copied into a project.
IGNORED_PATTERNS = (
    "__pycache__",
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "*.pyc",
    "*.pyo",
    ".DS_Store",
)


def _install_error(message: str, hint: str) -> Exception:
    """Build an :class:`InstallError` without a module level circular import."""
    from .installer_core import InstallError  # core imports this module

    return InstallError(message, hint)


def bundle_bases() -> List[Path]:
    """Return directories that may hold the bundled payload, best first."""
    bases: List[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bases.append(Path(meipass))
    try:
        bases.append(Path(sys.executable).resolve().parent)
    except (OSError, ValueError):  # pragma: no cover - defensive
        pass
    return bases


def repo_root() -> Path:
    """Return the repository root (parent of the ``installer`` package)."""
    return Path(__file__).resolve().parent.parent


def find_addon_source() -> Path:
    """Locate the ``addons/godot_mcp`` directory shipped with the installer.

    Search order:

    1. ``$GODOT_BRIDGE_ADDON_SOURCE``,
    2. PyInstaller payload (``_payload/addons/godot_mcp``),
    3. repository relative ``<repo>/Gohot_bridge_mcp/addons/godot_mcp``,
    4. current working directory.

    Returns:
        The addon source directory (contains ``plugin.cfg``).
    Raises:
        InstallError: When no directory containing ``plugin.cfg`` is found.
    """
    candidates: List[Path] = []
    override = os.environ.get(ENV_ADDON_SOURCE)
    if override:
        candidates.append(Path(override).expanduser())
    for base in bundle_bases():
        candidates.append(base / PAYLOAD_DIR / ADDON_RELPATH)
        candidates.append(base / ADDON_RELPATH)
    root = repo_root()
    candidates.append(root / REPO_DIR_NAME / ADDON_RELPATH)
    candidates.append(root / ADDON_RELPATH)
    try:
        cwd = Path.cwd()
    except OSError:  # pragma: no cover - deleted working directory
        cwd = root
    candidates.append(cwd / REPO_DIR_NAME / ADDON_RELPATH)
    candidates.append(cwd / ADDON_RELPATH)

    for candidate in candidates:
        try:
            if (candidate / PLUGIN_CFG_NAME).is_file():
                return candidate.resolve()
        except OSError:  # pragma: no cover - permission issues
            continue
    raise _install_error(
        "安装包不完整：找不到 Godot 插件文件（addons/godot_mcp/plugin.cfg）。",
        "请重新下载安装包；从源码运行时请确认仓库里的 Gohot_bridge_mcp/addons/godot_mcp 没有被移动。",
    )


def find_server_source(addon_source: Optional[Path] = None) -> Path:
    """Locate the ``server`` python package shipped with the installer.

    Args:
        addon_source: Optional addon directory used to derive the repository
            layout (``<repo>/Gohot_bridge_mcp/{addons/godot_mcp,server}``).
    Returns:
        Directory containing ``__init__.py`` and ``server.py``.
    Raises:
        InstallError: When the package cannot be found.
    """
    candidates: List[Path] = []
    override = os.environ.get(ENV_SERVER_SOURCE)
    if override:
        candidates.append(Path(override).expanduser())
    for base in bundle_bases():
        candidates.append(base / PAYLOAD_DIR / SERVER_PACKAGE)
        candidates.append(base / SERVER_PACKAGE)
    if addon_source is not None:
        base_addon = Path(addon_source).expanduser()
        candidates.append(base_addon.parent.parent / SERVER_PACKAGE)
    root = repo_root()
    candidates.append(root / REPO_DIR_NAME / SERVER_PACKAGE)
    candidates.append(root / SERVER_PACKAGE)

    for candidate in candidates:
        try:
            if (candidate / "__init__.py").is_file():
                return candidate.resolve()
        except OSError:  # pragma: no cover - permission issues
            continue
    raise _install_error(
        "安装包不完整：找不到 MCP 服务端代码（server/）。",
        "请重新下载安装包；从源码运行时请确认 Gohot_bridge_mcp/server 存在。",
    )


def copy_tree(source: Path, target: Path) -> int:
    """Copy ``source`` into ``target`` and return the number of copied files.

    Args:
        source: Existing directory (must not be inside ``target``).
        target: Destination directory, created by this call.
    Returns:
        Number of regular files written.
    """
    shutil.copytree(
        str(source),
        str(target),
        ignore=shutil.ignore_patterns(*IGNORED_PATTERNS),
    )
    return sum(1 for item in target.rglob("*") if item.is_file())


__all__ = [
    "IGNORED_PATTERNS",
    "PLUGIN_CFG_NAME",
    "bundle_bases",
    "copy_tree",
    "find_addon_source",
    "find_server_source",
    "repo_root",
]
