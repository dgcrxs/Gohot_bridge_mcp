# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: single-file windowed installer.

Build (from the repository root)::

    pip install pyinstaller
    pyinstaller installer/build.spec

The plugin and the python server package are bundled under ``_payload`` so the
frozen installer can copy them into the user's project without network access.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(SPECPATH).resolve().parent
APP_NAME = "GodotBridgeMCP-Setup"
PAYLOAD = "_payload"

#: Modules that are never needed by the installer (keeps the exe small).
#:
#: Measured size of the linux x86_64 onefile build (Python 3.12, PyInstaller
#: 6.22): 12.0 MB. That is far below the 40 MB budget, so the aggressive stdlib
#: trims (``unittest`` / ``email`` / ``http`` / ``xml`` / ``pydoc`` /
#: ``distutils``) are deliberately NOT enabled - they save a few MB but are easy
#: to get wrong. ``tkinter`` (and its ``tkinter.*`` submodules) must never be
#: excluded: the whole application is a Tk window.
#:
#: The interpreter that *runs* PyInstaller must itself be able to
#: ``import tkinter`` (i.e. it needs ``_tkinter``); otherwise the freeze either
#: fails with "No module named _tkinter" or produces a GUI that cannot start.
#: On Linux ``actions/setup-python`` interpreters may lack it, so
#: ``.github/workflows/release-installers.yml`` falls back to a venv built from
#: the system python in that case.
EXCLUDES = [
    "pytest",
    "_pytest",
    "numpy",
    "pandas",
    "scipy",
    "matplotlib",
    "PIL",
    "Pillow",
    "cv2",
    "torch",
    "sklearn",
    "IPython",
    "notebook",
    "jupyter",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "wx",
    "sqlalchemy",
    "django",
    "flask",
    "setuptools",
    "pkg_resources",
    "pydoc_data",
]


def collect_tree(source, prefix):
    """Return ``datas`` tuples that mirror ``source`` under ``prefix``."""
    entries = []
    source = Path(source)
    if not source.is_dir():
        print("[build.spec] missing payload directory: %s" % source)
        return entries
    for path in sorted(source.rglob("*")):
        if path.is_dir():
            continue
        if "__pycache__" in path.parts:
            continue
        if path.suffix in (".pyc", ".pyo"):
            continue
        relative = path.relative_to(source)
        destination = str(Path(prefix) / relative.parent)
        entries.append((str(path), destination))
    return entries


addon_dir = REPO_ROOT / "Gohot_bridge_mcp" / "addons" / "godot_mcp"
server_dir = REPO_ROOT / "Gohot_bridge_mcp" / "server"

datas = collect_tree(addon_dir, PAYLOAD + "/addons/godot_mcp")
datas += collect_tree(server_dir, PAYLOAD + "/server")
print("[build.spec] payload files: %d" % len(datas))

icon_path = REPO_ROOT / "icon.ico"
icon = str(icon_path) if icon_path.is_file() else None

analysis = Analysis(
    [str(REPO_ROOT / "installer" / "__main__.py")],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "installer",
        "installer.gui",
        "installer.installer_core",
        "installer.mcp_clients",
        "installer.project_file",
        "installer.server_runtime",
        "installer.sources",
        "installer.theme",
        "installer.venv_setup",
        "tkinter",
        "tkinter.filedialog",
        "tkinter.font",
        "tkinter.messagebox",
        "tkinter.scrolledtext",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)

if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name=APP_NAME + ".app",
        icon=None,
        bundle_identifier="io.godotbridgemcp.setup",
        info_plist={
            "CFBundleName": "Godot Bridge MCP Setup",
            "CFBundleShortVersionString": "2.0.0",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
        },
    )
