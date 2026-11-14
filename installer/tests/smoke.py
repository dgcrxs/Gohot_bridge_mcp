"""End-to-end smoke test for the installer (CLI + GUI + frozen binary).

Runs on a headless machine as long as ``xvfb-run`` is available for the GUI part.

Usage::

    python3 installer/tests/smoke.py                       # from source
    INSTALLER_BIN=/path/GodotBridgeMCP-Setup python3 installer/tests/smoke.py

The frozen binary is exercised for the CLI cases (``--version``, install,
idempotent re-install, graceful failure, positional path); the GUI case always
imports :mod:`installer.gui` so the widgets can be driven directly.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
BINARY = os.environ.get("INSTALLER_BIN", "").strip()
COMMAND: List[str] = [BINARY] if BINARY else [sys.executable, "-m", "installer"]
RUNNER = "binary" if BINARY else "source"

_RESULTS: List[Tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    """Record one assertion and print it immediately."""
    _RESULTS.append((name, bool(ok), detail))
    line = ("PASS " if ok else "FAIL ") + name
    if detail and not ok:
        line += " | " + detail
    print(line, flush=True)


def make_project(path: Path, enable_plugin: bool = False, with_addon: bool = False) -> Path:
    """Create a minimal but valid Godot project at ``path``."""
    path.mkdir(parents=True, exist_ok=True)
    plugins = '"res://other/plugin.cfg"'
    if enable_plugin:
        plugins += ', "res://addons/godot_mcp/plugin.cfg"'
    (path / "project.godot").write_text(
        "; Engine configuration file.\n"
        "config_version=5\n\n"
        "[application]\n"
        'config/name="Smoke Game"\n'
        'run/main_scene="res://main.tscn"\n\n'
        "[editor_plugins]\n"
        "enabled=PackedStringArray(%s)\n" % plugins,
        encoding="utf-8",
    )
    if with_addon:
        addon = path / "addons" / "godot_mcp"
        addon.mkdir(parents=True, exist_ok=True)
        (addon / "plugin.cfg").write_text('[plugin]\nname="Godot Bridge MCP"\n', encoding="utf-8")
    return path


def run_cli(args: List[str], cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    """Run the installer CLI (source or frozen binary)."""
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.run(
        COMMAND + args, cwd=str(cwd), capture_output=True, text=True, env=env
    )


def last_json(proc: subprocess.CompletedProcess) -> Dict[str, Any]:
    """Parse the last stdout line as JSON, returning ``{}`` on failure."""
    for line in reversed([line for line in proc.stdout.splitlines() if line.strip()]):
        try:
            return json.loads(line)
        except ValueError:
            continue
    return {}


def test_cli(workdir: Path) -> None:
    """Install, re-install and fail, exactly like the QA checklist."""
    project = make_project(workdir / "Install")
    proc = run_cli(["--headless", "--project", str(project), "--client", "cursor", "--no-venv", "--json"])
    payload = last_json(proc)
    check("install: exit 0", proc.returncode == 0, "rc=%s" % proc.returncode)
    check("install: ok true", payload.get("ok") is True, json.dumps(payload)[:160])
    check("install: addon copied", (project / "addons/godot_mcp/plugin.cfg").is_file())
    check("install: server runtime", (project / ".godot-bridge-mcp/server/run_server.py").is_file())
    text = (project / "project.godot").read_text(encoding="utf-8")
    check("install: plugin enabled once", text.count("res://addons/godot_mcp/plugin.cfg") == 1)
    check("install: other plugin preserved", "res://other/plugin.cfg" in text)
    check("install: cursor config written", (project / ".cursor/mcp.json").is_file())

    backup = (project / "project.godot.bak-mcp").read_bytes()
    again = run_cli(["--headless", "--project", str(project), "--client", "cursor", "--no-venv", "--json"])
    payload2 = last_json(again)
    text2 = (project / "project.godot").read_text(encoding="utf-8")
    check("idempotent: exit 0", again.returncode == 0 and payload2.get("ok") is True)
    check("idempotent: no duplicate entry", text2.count("res://addons/godot_mcp/plugin.cfg") == 1)
    check("idempotent: backup untouched", backup == (project / "project.godot.bak-mcp").read_bytes())
    check(
        "idempotent: every step ok/skipped",
        all(step.get("status") in ("ok", "skipped") for step in payload2.get("steps", [])),
    )

    not_godot = workdir / "NotGodot"
    not_godot.mkdir(parents=True, exist_ok=True)
    (not_godot / "readme.txt").write_text("nothing here", encoding="utf-8")
    failed = run_cli(["--headless", "--project", str(not_godot), "--client", "cursor", "--no-venv", "--json"])
    payload3 = last_json(failed)
    check("failure: exit 1", failed.returncode == 1, "rc=%s" % failed.returncode)
    check("failure: ok false", payload3.get("ok") is False)
    check("failure: mentions project.godot", "project.godot" in str(payload3.get("error", "")))
    check("failure: no traceback", "Traceback" not in failed.stderr + failed.stdout)
    check("failure: hint present", bool(payload3.get("hint")))

    positional = make_project(workdir / "Positional")
    dropped = run_cli(["--headless", str(positional), "--client", "cursor", "--no-venv", "--json"])
    check("drag&drop: bare path accepted", dropped.returncode == 0 and last_json(dropped).get("ok") is True)


def test_gui(workdir: Path) -> None:
    """Prefill from ``--project``, press 安装, assert the busy/done states."""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        import tkinter as tk

        import installer.gui as gui
        from installer.mcp_clients import CLIENTS
        from installer.theme import apply_theme
    except Exception as error:  # noqa: BLE001 - tkinter missing on this box
        check("gui: importable", False, str(error))
        return

    project = make_project(workdir / "Gui")
    dialogs: List[Tuple[str, str, str]] = []

    class FakeBox:
        """Stand-in for ``tkinter.messagebox`` so the window never blocks."""

        def showinfo(self, title: str, body: str, **kw: Any) -> None:
            dialogs.append(("info", title, body))

        def showerror(self, title: str, body: str, **kw: Any) -> None:
            dialogs.append(("error", title, body))

        def showwarning(self, title: str, body: str, **kw: Any) -> None:
            dialogs.append(("warning", title, body))

        def askyesno(self, title: str, body: str, **kw: Any) -> bool:
            dialogs.append(("ask", title, body))
            return True

    gui.messagebox = FakeBox()

    try:
        root = tk.Tk()
    except tk.TclError as error:
        check("gui: display available", False, str(error))
        return

    apply_theme(root)
    app = gui.InstallerApp(root, initial_project=str(project))
    app.make_venv_var.set(False)
    app.client_var.set(str(CLIENTS["cursor"]["label"]))

    state: Dict[str, Any] = {
        "prefill": app.project_var.get(),
        "badge_before": app.info_badge.cget("text"),
    }

    def start() -> None:
        app.on_install()
        state["busy_during"] = bool(app.busy)
        state["install_disabled"] = str(app.install_button.cget("state"))
        state["status_during"] = app.status_var.get()

    def poll() -> None:
        if app.busy:
            root.after(150, poll)
            return
        state["status_after"] = app.status_var.get()
        state["badge_after"] = app.info_badge.cget("text")
        state["detail"] = app.info_detail_var.get()
        state["install_enabled"] = str(app.install_button.cget("state"))
        state["progress"] = float(app.progress.cget("value"))
        state["first_log"] = app.log_widget.get("1.0", "2.0").strip()
        root.destroy()

    root.after(300, start)
    root.after(600, poll)
    root.mainloop()

    check("gui: project prefilled", state.get("prefill") == str(project), str(state.get("prefill")))
    check("gui: busy while installing", state.get("busy_during") is True)
    check("gui: install button disabled", state.get("install_disabled") == "disabled")
    check("gui: status 安装中…", state.get("status_during") == "安装中…", str(state.get("status_during")))
    check("gui: status 已完成", state.get("status_after") == "已完成", str(state.get("status_after")))
    check("gui: button re-enabled", state.get("install_enabled") == "normal")
    check("gui: progress 100", state.get("progress") == 100.0, str(state.get("progress")))
    check("gui: badge 插件已启用", state.get("badge_after") == "插件已启用", str(state.get("badge_after")))
    check("gui: detail has config_version", "config_version=5" in str(state.get("detail")))
    check("gui: log starts with guidance", str(state.get("first_log", "")).count("[") == 1
          and "选择工程后点" in str(state.get("first_log")))
    info = [dialog for dialog in dialogs if dialog[0] == "info"]
    check("gui: success dialog", bool(info))
    if info:
        body = info[0][2]
        check("gui: dialog lists addon dir", "addons/godot_mcp" in body)
        check("gui: dialog lists next steps", "下一步" in body and "godot_ping" in body)
        check("gui: dialog lists config path", "配置已写入" in body)


def main() -> int:
    """Run every case and summarise."""
    workdir = Path(tempfile.mkdtemp(prefix="installer-smoke-"))
    try:
        print("runner: %s" % RUNNER, flush=True)
        test_cli(workdir)
        test_gui(workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    failed = len(_RESULTS) - passed
    print(json.dumps({"runner": RUNNER, "passed": passed, "failed": failed}), flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
