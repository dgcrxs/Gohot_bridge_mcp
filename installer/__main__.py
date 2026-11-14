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
import io
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


def _write_line(text: str, stream: Any = None) -> bool:
    """Write ``text`` to ``stream`` (stdout by default) without ever raising.

    A closed pipe or a console that cannot encode the character must never turn
    a successful installation into a crash, so the failure is swallowed.

    Args:
        text: Line to write (a newline is appended).
        stream: Target stream, ``sys.stdout`` when omitted.
    Returns:
        True when the text reached the stream.
    """
    target = sys.stdout if stream is None else stream
    if target is None:
        return False
    try:
        print(text, file=target, flush=True)
        return True
    except (UnicodeEncodeError, ValueError, OSError):
        return False


def _say(message: str = "", *, stream: Any = None, end: str = "\n") -> None:
    """Print one line, degrading to ``errors="replace"`` when encoding fails.

    Args:
        message: Text to print (may contain Chinese).
        stream: Target stream, ``sys.stdout`` when omitted.
        end: String appended after ``message``.
    """
    target = sys.stdout if stream is None else stream
    if target is None:
        return
    try:
        print(message, file=target, end=end, flush=True)
        return
    except UnicodeEncodeError:
        pass
    except (ValueError, OSError):
        return
    encoding = getattr(target, "encoding", None) or "ascii"
    try:
        safe = message.encode(encoding, errors="replace").decode(encoding, errors="replace")
    except (LookupError, UnicodeError):
        safe = message.encode("ascii", errors="replace").decode("ascii")
    try:
        print(safe, file=target, end=end, flush=True)
    except (UnicodeEncodeError, ValueError, OSError):
        pass


def _log(message: str) -> None:
    """Print one progress line (stdout, flushed immediately)."""
    _say("[%s] %s" % (_stamp(), message))


def _has_display() -> bool:
    """Return True when a graphical session is available."""
    if sys.platform.startswith("win") or sys.platform == "darwin":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


#: Original streams, kept alive so replacing ``sys.stdout`` never closes them.
_ORIGINAL_STREAMS: List[Any] = []


def _configure_stream(name: str) -> None:
    """Force ``sys.<name>`` to UTF-8 so Chinese output never raises.

    ``reconfigure`` is the cheapest route (Python 3.7+); when the stream does
    not support it (PyInstaller's windowed placeholder, exotic embedders) a new
    :class:`io.TextIOWrapper` is wrapped around the raw buffer instead.

    Args:
        name: ``"stdout"`` or ``"stderr"``.
    """
    stream = getattr(sys, name, None)
    if stream is None:
        return
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
        return
    except (AttributeError, ValueError, OSError):
        pass
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        return
    try:
        wrapper = io.TextIOWrapper(
            buffer,
            encoding="utf-8",
            errors="replace",
            line_buffering=True,
            write_through=True,
        )
    except (ValueError, OSError):
        return
    # The replaced wrapper must stay reachable, otherwise its finaliser closes
    # the underlying buffer and the new wrapper becomes unusable.
    _ORIGINAL_STREAMS.append(stream)
    try:
        setattr(sys, name, wrapper)
    except (AttributeError, TypeError):  # pragma: no cover - exotic embedders
        _ORIGINAL_STREAMS.pop()


def _ensure_streams() -> None:
    """Give the windowed (``console=False``) build usable UTF-8 stdout/stderr.

    PyInstaller's windowed executables start with ``sys.stdout`` set to ``None``;
    any ``print`` would then raise ``AttributeError`` instead of showing a
    message. Pointing both streams at ``os.devnull`` keeps the CLI paths quiet
    but alive when the binary is launched by double click.

    Streams that *do* exist are reconfigured to UTF-8 with ``errors="replace"``:
    on a Chinese Windows the console encoding is ``cp936`` (and ``cp1252`` on a
    western one), which cannot encode the Chinese text of the JSON report and
    used to abort the whole run with ``UnicodeEncodeError``.
    """
    if sys.platform.startswith("win"):
        # Child processes (pip, venv, the MCP client) inherit this and then
        # print UTF-8 as well.
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            try:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8", errors="replace"))
            except OSError:  # pragma: no cover - nothing left to try
                pass
            continue
        _configure_stream(name)


def _emit_json(payload: Dict[str, Any]) -> None:
    """Print ``payload`` as a single line of JSON on stdout.

    Never raises: if the stream cannot encode the non-ASCII characters of the
    report the payload is re-encoded with ``ensure_ascii=True`` and marked with
    ``"ascii_fallback": true`` so the caller still receives valid JSON.
    """
    data: Dict[str, Any] = dict(payload)
    data.pop("ascii_fallback", None)
    try:
        encoded = json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError):
        encoded = ""
    if encoded and _write_line(encoded):
        return

    fallback: Dict[str, Any] = dict(data)
    fallback["ascii_fallback"] = True
    try:
        encoded = json.dumps(fallback, ensure_ascii=True)
    except (TypeError, ValueError):
        encoded = '{"ok": false, "ascii_fallback": true, "error": "result could not be serialised"}'
    _write_line(encoded)


def _human_result(result: Dict[str, Any]) -> None:
    """Print an installation result in a human friendly form."""
    steps = result.get("steps") or []
    _say("")
    _say("步骤：")
    for step in steps:
        status = str(step.get("status", ""))
        marker = {"ok": "OK  ", "skipped": "SKIP", "failed": "FAIL"}.get(status, status.upper())
        _say("  [%s] %-8s %s" % (marker, step.get("name", ""), step.get("detail", "")))
    config = result.get("client_config") or {}
    _say("")
    if result.get("ok"):
        _say("安装完成。")
    _say("Python: %s" % result.get("python", ""))
    if config:
        _say("客户端: %s" % config.get("label", config.get("client", "")))
        if config.get("written"):
            _say("配置文件: %s" % config.get("path", ""))
        else:
            _say("需要手动粘贴以下片段：")
            _say(str(config.get("snippet", "")))


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
    stderr = sys.stderr
    _say("")
    _say("错误：%s" % error.user_message, stream=stderr)
    if error.hint:
        _say("提示：%s" % error.hint, stream=stderr)
    if error.detail:
        _say("详情：", stream=stderr)
        for line in error.detail.splitlines():
            _say("  | %s" % line, stream=stderr)
    for step in getattr(error, "steps", []) or []:
        if step.get("status") == "failed":
            _say("失败步骤：%s" % step.get("name", ""), stream=stderr)


def _run_headless(args: argparse.Namespace) -> int:
    """Run the installer without a GUI. Returns the process exit code."""
    if not args.project:
        flag = "--uninstall" if args.uninstall else "--headless"
        _say("错误：%s 需要 --project <Godot 工程目录>。" % flag, stream=sys.stderr)
        _say("示例：python -m installer --headless --project ~/MyGame --client claude", stream=sys.stderr)
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
            _say("卸载完成：%s" % result.get("project", ""))
            _say("客户端配置与虚拟环境已保留，可按需手动删除。")
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
        _say("已取消。", stream=sys.stderr)
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
        _say("%s Installer %s" % (APP_NAME, VERSION))
        _say("clients: %s" % ", ".join(CLIENTS[key]["label"] for key in client_keys()))
        return EXIT_OK

    if args.project and args.path:
        _say("错误：--project 与位置参数 PATH 只能给一个。", stream=sys.stderr)
        return EXIT_USAGE
    # A bare positional argument is what a drag & drop onto the icon produces.
    if not args.project and args.path:
        args.project = args.path

    if args.headless or args.uninstall:
        return _run_headless(args)

    if not _has_display():
        _say("错误：当前环境没有图形界面（DISPLAY 未设置），无法打开安装器窗口。", stream=sys.stderr)
        _say(
            "请改用命令行模式：python -m installer --headless --project <工程目录> --client claude",
            stream=sys.stderr,
        )
        return EXIT_USAGE

    from installer.gui import run_gui  # imported lazily: the CLI must work without tkinter

    try:
        return int(run_gui(initial_project=args.project))
    except Exception as exc:  # noqa: BLE001 - never show a traceback to end users
        _say("错误：安装器界面启动失败：%s" % exc, stream=sys.stderr)
        _say("可改用命令行模式：python -m installer --headless --project <工程目录>", stream=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
