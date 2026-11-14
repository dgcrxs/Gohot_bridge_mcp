"""Command line entry point for the Godot Bridge MCP installer.

Usage::

    python -m installer                      # graphical installer
    python -m installer --project PATH       # graphical installer, project prefilled
    python -m installer PATH                 # same, for drag & drop onto the icon
    python -m installer --headless --project PATH --client claude [--no-venv] [--json]
    python -m installer --uninstall --project PATH

Exit codes: ``0`` success, ``1`` installation failed, ``2`` bad usage.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

if __package__ in (None, ""):  # run as a plain script or from PyInstaller
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from installer import APP_NAME, VERSION  # noqa: E402
from installer.installer_core import InstallError, run_install, uninstall  # noqa: E402
from installer.mcp_clients import CLIENTS, DEFAULT_CLIENT, client_keys  # noqa: E402

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for ``python -m installer``."""
    parser = argparse.ArgumentParser(
        prog="python -m installer",
        description="%s Installer %s - 一键把 Godot 插件、Python 依赖和 MCP 客户端配置装好。" % (APP_NAME, VERSION),
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="不打开窗口，直接在终端里安装（适合 CI 和 QA）。",
    )
    parser.add_argument(
        "--project",
        default=None,
        metavar="PATH",
        help="Godot 工程根目录（含 project.godot）。",
    )
    parser.add_argument(
        "--client",
        default=DEFAULT_CLIENT,
        choices=client_keys(),
        help="要配置的 MCP 客户端，默认 %(default)s。",
    )
    parser.add_argument(
        "--no-venv",
        dest="no_venv",
        action="store_true",
        help="跳过独立虚拟环境（使用当前 Python，仅推荐给已装好依赖的用户）。",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="以单行 JSON 输出结果（配合 --headless）。",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="卸载：从工程里移除插件并禁用，保留客户端配置与虚拟环境。",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="打印版本号并退出。",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        metavar="PATH",
        help="可选的工程目录，等同 --project（方便把工程文件夹拖到安装器图标上）。",
    )
    return parser


def _stamp() -> str:
    """Return the current time as ``HH:MM:SS``."""
    return datetime.now().strftime("%H:%M:%S")


def _log(message: str) -> None:
    """Print one progress line (stdout, flushed immediately)."""
    print("[%s] %s" % (_stamp(), message), flush=True)


def _has_display() -> bool:
    """Return True when a graphical session is available."""
    if sys.platform.startswith("win") or sys.platform == "darwin":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _ensure_streams() -> None:
    """Give the windowed (``console=False``) build usable stdout/stderr.

    PyInstaller's windowed executables start with ``sys.stdout`` set to ``None``;
    any ``print`` would then raise ``AttributeError`` instead of showing a
    message. Pointing both streams at ``os.devnull`` keeps the CLI paths quiet
    but alive when the binary is launched by double click.
    """
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            try:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            except OSError:  # pragma: no cover - nothing left to try
                pass


def _emit_json(payload: Dict[str, Any]) -> None:
    """Print ``payload`` as a single line of JSON on stdout."""
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _human_result(result: Dict[str, Any]) -> None:
    """Print an installation result in a human friendly form."""
    steps = result.get("steps") or []
    print("", flush=True)
    print("步骤：", flush=True)
    for step in steps:
        status = str(step.get("status", ""))
        marker = {"ok": "OK  ", "skipped": "SKIP", "failed": "FAIL"}.get(status, status.upper())
        print("  [%s] %-8s %s" % (marker, step.get("name", ""), step.get("detail", "")), flush=True)
    config = result.get("client_config") or {}
    print("", flush=True)
    if result.get("ok"):
        print("安装完成。", flush=True)
    print("Python: %s" % result.get("python", ""), flush=True)
    if config:
        print("客户端: %s" % config.get("label", config.get("client", "")), flush=True)
        if config.get("written"):
            print("配置文件: %s" % config.get("path", ""), flush=True)
        else:
            print("需要手动粘贴以下片段：", flush=True)
            print(str(config.get("snippet", "")), flush=True)


def _failure_payload(error: InstallError) -> Dict[str, Any]:
    """Build the JSON payload describing a failure."""
    return {
        "ok": False,
        "error": error.user_message,
        "hint": error.hint,
        "detail": error.detail,
        "steps": getattr(error, "steps", []) or [],
    }


def _print_failure(error: InstallError) -> None:
    """Print a failure in the human readable format."""
    print("", flush=True)
    print("错误：%s" % error.user_message, file=sys.stderr, flush=True)
    if error.hint:
        print("提示：%s" % error.hint, file=sys.stderr, flush=True)
    if error.detail:
        print("详情：", file=sys.stderr, flush=True)
        for line in error.detail.splitlines():
            print("  | %s" % line, file=sys.stderr, flush=True)
    for step in getattr(error, "steps", []) or []:
        if step.get("status") == "failed":
            print("失败步骤：%s" % step.get("name", ""), file=sys.stderr, flush=True)


def _run_headless(args: argparse.Namespace) -> int:
    """Run the installer without a GUI. Returns the process exit code."""
    if not args.project:
        flag = "--uninstall" if args.uninstall else "--headless"
        print("错误：%s 需要 --project <Godot 工程目录>。" % flag, file=sys.stderr, flush=True)
        print("示例：python -m installer --headless --project ~/MyGame --client claude", file=sys.stderr, flush=True)
        return EXIT_USAGE

    progress = None if args.as_json else _log
    project = Path(args.project).expanduser()

    if args.uninstall:
        try:
            result = uninstall(project, progress=progress)
        except InstallError as error:
            if args.as_json:
                _emit_json(_failure_payload(error))
            else:
                _print_failure(error)
            return EXIT_FAILED
        if args.as_json:
            _emit_json(result)
        else:
            print("卸载完成：%s" % result.get("project", ""), flush=True)
            print("客户端配置与虚拟环境已保留，可按需手动删除。", flush=True)
        return EXIT_OK

    try:
        result = run_install(
            project,
            client=args.client,
            make_venv=not args.no_venv,
            progress=progress,
        )
    except InstallError as error:
        if args.as_json:
            _emit_json(_failure_payload(error))
        else:
            _print_failure(error)
        return EXIT_FAILED
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        print("已取消。", file=sys.stderr, flush=True)
        return EXIT_FAILED

    if args.as_json:
        _emit_json(result)
    else:
        _human_result(result)
    return EXIT_OK


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point shared by ``python -m installer`` and the frozen exe."""
    _ensure_streams()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse already printed the usage message
        return int(exc.code or EXIT_USAGE)

    if args.version:
        print("%s Installer %s" % (APP_NAME, VERSION), flush=True)
        print("clients: %s" % ", ".join(CLIENTS[key]["label"] for key in client_keys()), flush=True)
        return EXIT_OK

    if args.project and args.path:
        print(
            "错误：--project 与位置参数 PATH 只能给一个。",
            file=sys.stderr,
            flush=True,
        )
        return EXIT_USAGE
    # A bare positional argument is what a drag & drop onto the icon produces.
    if not args.project and args.path:
        args.project = args.path

    if args.headless or args.uninstall:
        return _run_headless(args)

    if not _has_display():
        print("错误：当前环境没有图形界面（DISPLAY 未设置），无法打开安装器窗口。", file=sys.stderr, flush=True)
        print(
            "请改用命令行模式：python -m installer --headless --project <工程目录> --client claude",
            file=sys.stderr,
            flush=True,
        )
        return EXIT_USAGE

    from installer.gui import run_gui  # imported lazily: the CLI must work without tkinter

    try:
        return int(run_gui(initial_project=args.project))
    except Exception as exc:  # noqa: BLE001 - never show a traceback to end users
        print("错误：安装器界面启动失败：%s" % exc, file=sys.stderr, flush=True)
        print("可改用命令行模式：python -m installer --headless --project <工程目录>", file=sys.stderr, flush=True)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
