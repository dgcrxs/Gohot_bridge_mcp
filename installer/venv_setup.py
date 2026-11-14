"""Creation and population of the installer's isolated Python environment.

The venv lives outside the Godot project (``~/.godot-bridge-mcp/venv`` by
default) so it is shared between projects and can be deleted at any time
without touching the user's system Python.

Only the standard library is used.
"""

from __future__ import annotations

import ntpath
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from .installer_core import InstallError

#: Packages the MCP server imports at runtime.
REQUIRED_PACKAGES: Sequence[str] = ("mcp>=1.30.0", "websockets>=12.0")
#: Default location of the shared virtualenv.
DEFAULT_VENV_DIR = Path.home() / ".godot-bridge-mcp" / "venv"
#: How many output lines are kept as error detail.
OUTPUT_TAIL = 15
#: Hard cap for the in-memory output buffer.
MAX_BUFFER_LINES = 600
#: Timeouts (seconds) for the expensive subprocess calls.
VENV_TIMEOUT = 600
PIP_TIMEOUT = 1800
PROBE_TIMEOUT = 120
#: Timeout (seconds) for a single interpreter discovery probe.
INTERPRETER_TIMEOUT = 20

#: Python version requested from the ``py`` launcher when no real
#: interpreter can be found (``py -3 <script>``).
LAUNCHER_VERSION_ARGS: Sequence[str] = ("-3",)
#: The ``py`` launcher is *not* an interpreter: it picks a version and then
#: re-executes ``python.exe``. Clients that spawn ``command`` directly would
#: get the launcher instead of a real interpreter, so it never goes into a
#: configuration file as ``command``.
LAUNCHER_NAMES = frozenset(("py", "py.exe"))

#: True on Windows (both CPython and the frozen installer).
_IS_WINDOWS = sys.platform.startswith("win")
#: ``python.exe`` / ``python3.exe`` / ``python3.11.exe`` / ``pythonw.exe``.
_PYTHON_NAME_RE = re.compile(r"^python(?:\d+(?:\.\d+)?)?w?(?:\.exe)?$", re.IGNORECASE)
#: ``C:`` style drive prefix (lets us treat Windows paths on any platform).
_DRIVE_RE = re.compile(r"^[a-zA-Z]:")
#: Splits a path on either separator, used to spot PyInstaller's ``_MEIxxxx``.
_SEPARATORS_RE = re.compile(r"[\\/]+")

ProgressCallback = Optional[Callable[[str], None]]


# --------------------------------------------------------------------- helpers


def _emit(progress: ProgressCallback, message: str) -> None:
    """Call ``progress`` when provided, never raising into the caller."""
    if progress is None:
        return
    try:
        progress(message)
    except Exception:  # noqa: BLE001 - a broken callback must not break installs
        pass


def _no_window_flags() -> int:
    """Return subprocess flags that suppress console windows on Windows."""
    if sys.platform.startswith("win"):
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return 0


def venv_python(venv_dir: Path) -> Path:
    """Return the interpreter path inside ``venv_dir`` for this platform."""
    directory = Path(venv_dir).expanduser()
    if sys.platform.startswith("win"):
        return directory / "Scripts" / "python.exe"
    return directory / "bin" / "python"


def normalize_path(path: str) -> str:
    """Return ``path`` normalised with :mod:`ntpath` on Windows.

    Windows paths are always normalised with ``ntpath`` (even when this code
    runs on POSIX during tests) so a configuration file never contains mixed
    separators such as ``C:/Py311\\\\python.exe``.

    Args:
        path: Any file system path.
    Returns:
        The normalised path, or ``""`` when ``path`` is empty.
    """
    text = str(path or "")
    if not text:
        return text
    if _IS_WINDOWS:
        return ntpath.normpath(ntpath.abspath(text))
    if _DRIVE_RE.match(text) or "\\" in text:
        return ntpath.normpath(text)
    return os.path.abspath(text)


def path_basename(path: str) -> str:
    """Return the file name of ``path`` for either path flavour."""
    return ntpath.basename(str(path or ""))


def is_launcher(path: str) -> bool:
    """Return True when ``path`` is the ``py`` launcher, not an interpreter."""
    return path_basename(path).lower() in LAUNCHER_NAMES


def looks_like_python(path: str) -> bool:
    """Return True when the file name is a CPython executable name."""
    return bool(_PYTHON_NAME_RE.match(path_basename(path)))


def _is_extraction_path(path: str) -> bool:
    """Return True for PyInstaller's temporary ``_MEIxxxx`` extraction dir."""
    for part in _SEPARATORS_RE.split(str(path or "")):
        if part.lower().startswith("_mei"):
            return True
    return False


def _where_windows(name: str) -> List[str]:
    """Return every match reported by the Windows ``where`` utility."""
    try:
        completed = subprocess.run(
            ["where", name],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=INTERPRETER_TIMEOUT,
            creationflags=_no_window_flags(),
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    if completed.returncode != 0:
        return []
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def _where(name: str) -> List[str]:
    """Return every executable called ``name`` on ``PATH`` (unique, sorted by PATH).

    On Windows ``where`` is used first because it reports *all* matches;
    :func:`shutil.which` covers the case where ``where.exe`` is unavailable.
    """
    matches: List[str] = []
    if _IS_WINDOWS:
        matches.extend(_where_windows(name))
    if not matches:
        found = shutil.which(name)
        if found:
            matches.append(found)
    unique: List[str] = []
    seen = set()
    for item in matches:
        normalised = normalize_path(item)
        key = normalised.lower() if _IS_WINDOWS else normalised
        if key in seen:
            continue
        seen.add(key)
        unique.append(normalised)
    return unique


def _windows_install_roots() -> List[str]:
    """Return the directories a CPython installer usually writes to."""
    roots: List[str] = []
    drive = (os.environ.get("SystemDrive") or "C:") + "\\"
    roots.append(drive)  # catches C:\\Py311, C:\\Python311, ...
    roots.append(os.path.join(drive, "Program Files"))
    roots.append(os.path.join(drive, "Program Files (x86)"))
    for variable in ("LOCALAPPDATA", "APPDATA", "ProgramFiles", "ProgramFiles(x86)", "USERPROFILE"):
        value = os.environ.get(variable) or ""
        if not value:
            continue
        roots.append(os.path.join(value, "Programs", "Python"))
        roots.append(os.path.join(value, "Programs"))
    unique: List[str] = []
    for root in roots:
        normalised = normalize_path(root)
        if normalised not in unique:
            unique.append(normalised)
    return unique


def _common_install_pythons() -> List[str]:
    """Scan the usual Windows install roots for a real ``python.exe``.

    A Python installed without "Add python.exe to PATH" is invisible to
    ``where``/``py``; scanning ``C:\\Py311`` style directories keeps the
    installer working for those users (and is the last resort before falling
    back to the ``py`` launcher).
    """
    if not _IS_WINDOWS:
        return []
    found: List[str] = []
    for root in _windows_install_roots():
        try:
            names = sorted(os.listdir(root), reverse=True)
        except OSError:
            continue
        for name in names:
            lowered = name.lower()
            if not lowered.startswith(("python3", "py3")):
                continue
            candidate = os.path.join(root, name, "python.exe")
            if os.path.isfile(candidate):
                found.append(normalize_path(candidate))
    return found


def _probe_interpreter(candidate: str, launcher_args: Sequence[str] = ()) -> str:
    """Ask ``candidate`` for its own ``sys.executable``.

    Running the candidate is the only reliable way to tell a real interpreter
    from a launcher, a shim (``WindowsApps\\python.exe``) or a broken install.

    Args:
        candidate: Executable to run (interpreter or ``py`` launcher).
        launcher_args: Extra arguments in front of ``-c`` (``py`` needs ``-3``).
    Returns:
        The normalised interpreter path, or ``""`` when the probe failed.
    """
    command = [
        str(candidate),
        *[str(arg) for arg in launcher_args],
        "-c",
        "import sys;print(sys.executable)",
    ]
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=INTERPRETER_TIMEOUT,
            creationflags=_no_window_flags(),
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    if completed.returncode != 0:
        return ""
    for line in reversed(completed.stdout.splitlines()):
        text = line.strip()
        if not text or text.startswith(("{", "Traceback")):
            continue
        if not looks_like_python(text) or is_launcher(text):
            continue
        return normalize_path(text)
    return ""


def interpreter_candidates() -> List[str]:
    """Return the real interpreter candidates in discovery order.

    Order: ``sys.executable`` (unless it is the PyInstaller payload or the
    ``py`` launcher), then ``where python3`` / ``where python``.
    """
    candidates: List[str] = []
    executable = getattr(sys, "executable", "") or ""
    if (
        executable
        and not _is_extraction_path(executable)
        and not is_launcher(executable)
        and looks_like_python(executable)
    ):
        candidates.append(normalize_path(executable))
    for name in ("python3", "python"):
        candidates.extend(_where(name))
    return candidates


def launcher_candidates() -> List[str]:
    """Return every ``py`` launcher found on ``PATH``."""
    return [path for path in _where("py") if is_launcher(path)]


def resolve_interpreter() -> Dict[str, Any]:
    """Find a Python interpreter that MCP clients can execute directly.

    Never returns the ``py`` launcher as ``python``: when only the launcher is
    available the returned command is ``py`` plus :data:`LAUNCHER_VERSION_ARGS`
    so the client runs ``py -3 <script>`` instead of pretending that
    ``C:\\windows\\py.exe`` is an interpreter.

    Returns:
        ``{"python": str, "args": List[str], "launcher": bool, "note": str}``
        where ``args`` are inserted before the server script.
    Raises:
        InstallError: If neither an interpreter nor the launcher exists.
    """
    for candidate in interpreter_candidates():
        real = _probe_interpreter(candidate)
        if real:
            return {"python": real, "args": [], "launcher": False, "note": ""}

    for candidate in _common_install_pythons():
        real = _probe_interpreter(candidate)
        if real:
            return {"python": real, "args": [], "launcher": False, "note": ""}

    for launcher in launcher_candidates():
        real = _probe_interpreter(launcher, LAUNCHER_VERSION_ARGS)
        if real:
            return {"python": real, "args": [], "launcher": False, "note": ""}

    if launcher_candidates():
        # ``py`` exists but cannot resolve a version: let the client call the
        # launcher explicitly instead of writing a launcher path as interpreter.
        return {
            "python": "py",
            "args": list(LAUNCHER_VERSION_ARGS),
            "launcher": True,
            "note": "using py launcher",
        }

    raise InstallError(
        "找不到系统 Python 解释器，无法创建虚拟环境。",
        hint=(
            "请先从 https://www.python.org/downloads/ 安装 Python 3.10 或更新版本，"
            "安装时勾选 Add python.exe to PATH，然后重新运行安装器。"
        ),
    )


def base_python_command() -> List[str]:
    """Return the command prefix (interpreter + args) that runs Python.

    Raises:
        InstallError: If no usable interpreter can be found.
    """
    info = resolve_interpreter()
    return [str(info["python"])] + [str(arg) for arg in info["args"]]


def base_python() -> str:
    """Return the interpreter used to *create* the venv.

    When frozen by PyInstaller ``sys.executable`` is the installer itself, so a
    real interpreter has to be discovered through :func:`resolve_interpreter`.

    Raises:
        InstallError: If no usable interpreter can be found.
    """
    return base_python_command()[0]


def _run(
    command: Sequence[str],
    progress: ProgressCallback = None,
    *,
    timeout: int = PIP_TIMEOUT,
) -> tuple[int, List[str]]:
    """Run ``command`` streaming its output to ``progress``.

    Args:
        command: Argument vector, already fully resolved.
        progress: Optional callback receiving each output line.
        timeout: Seconds before the process is killed.
    Returns:
        ``(returncode, lines)`` where ``lines`` is capped at
        :data:`MAX_BUFFER_LINES` (the tail is kept).
    Raises:
        InstallError: If the executable cannot be started.
    """
    _emit(progress, "$ " + " ".join(str(part) for part in command))
    try:
        process = subprocess.Popen(
            [str(part) for part in command],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=_no_window_flags(),
        )
    except FileNotFoundError as exc:
        raise InstallError(
            "无法启动命令：%s" % command[0],
            hint="请确认 Python 已正确安装并加入 PATH，然后重新打开安装器。",
        ) from exc
    except OSError as exc:  # pragma: no cover - exotic platform failures
        raise InstallError(
            "无法启动命令：%s（%s）" % (command[0], exc),
            hint="请检查 Python 安装是否完整。",
        ) from exc

    killer: Optional[threading.Timer] = None
    if timeout:
        killer = threading.Timer(float(timeout), process.kill)
        killer.daemon = True
        killer.start()

    lines: List[str] = []
    try:
        stream = process.stdout
        if stream is not None:
            for raw in stream:
                text = raw.rstrip("\r\n")
                if not text:
                    continue
                lines.append(text)
                if len(lines) > MAX_BUFFER_LINES:
                    del lines[0]
                _emit(progress, text)
            stream.close()
        code = process.wait()
    finally:
        if killer is not None:
            killer.cancel()
    return code, lines


def _tail(lines: Sequence[str]) -> str:
    """Return the last :data:`OUTPUT_TAIL` lines as a single string."""
    return "\n".join(list(lines)[-OUTPUT_TAIL:])


def _probe(python: Path, code: str) -> bool:
    """Return True when ``python -c code`` exits with 0."""
    try:
        completed = subprocess.run(
            [str(python), "-c", code],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            timeout=PROBE_TIMEOUT,
            creationflags=_no_window_flags(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _has_requirements(python: Path) -> bool:
    """Return True when the interpreter can import every required package."""
    return _probe(python, "import mcp, websockets")


def _installed_packages(python: Path) -> List[str]:
    """Return a short ``pip freeze`` listing for the packages we care about."""
    try:
        completed = subprocess.run(
            [str(python), "-m", "pip", "freeze"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PROBE_TIMEOUT,
            creationflags=_no_window_flags(),
        )
    except (OSError, subprocess.SubprocessError):
        return list(REQUIRED_PACKAGES)
    if completed.returncode != 0:
        return list(REQUIRED_PACKAGES)
    wanted = ("mcp", "websockets", "anyio", "pydantic", "httpx", "starlette", "uvicorn")
    packages = [
        line.strip()
        for line in completed.stdout.splitlines()
        if line.strip().split("==")[0].lower() in wanted
    ]
    return packages or list(REQUIRED_PACKAGES)


def _requirement_specs(requirements_file: Optional[Path]) -> List[str]:
    """Read a requirements file, falling back to :data:`REQUIRED_PACKAGES`."""
    if requirements_file is None:
        return list(REQUIRED_PACKAGES)
    path = Path(requirements_file).expanduser()
    if not path.is_file():
        return list(REQUIRED_PACKAGES)
    specs: List[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        specs.append(stripped)
    return specs or list(REQUIRED_PACKAGES)


# ------------------------------------------------------------------ public API


def setup_venv(
    *,
    venv_dir: Optional[Path] = None,
    progress: ProgressCallback = None,
    requirements_file: Optional[Path] = None,
    upgrade_pip: bool = True,
) -> Dict[str, object]:
    """Create (or reuse) the venv and install the Python dependencies.

    The call is idempotent: an existing environment that can already import
    ``mcp`` and ``websockets`` is reused without touching pip.

    Args:
        venv_dir: Target directory, defaults to :data:`DEFAULT_VENV_DIR`.
        progress: Callback receiving human readable progress lines.
        requirements_file: Optional ``requirements.txt`` to install.
        upgrade_pip: Refresh pip before installing the dependencies.
    Returns:
        ``{"python", "venv_dir", "packages", "created", "installed"}``.
    Raises:
        InstallError: When the venv cannot be created or pip fails.
    """
    target = Path(venv_dir).expanduser() if venv_dir is not None else DEFAULT_VENV_DIR
    target = target.absolute()
    python = venv_python(target)

    if python.is_file() and _has_requirements(python):
        _emit(progress, "检测到可用的虚拟环境，跳过依赖安装：%s" % target)
        return {
            "python": str(python),
            "venv_dir": str(target),
            "packages": _installed_packages(python),
            "created": False,
            "installed": False,
        }

    if not python.is_file():
        _emit(progress, "创建独立 Python 环境：%s" % target)
        target.parent.mkdir(parents=True, exist_ok=True)
        interpreter = base_python_command()
        code, lines = _run(
            [*interpreter, "-m", "venv", str(target)],
            progress,
            timeout=VENV_TIMEOUT,
        )
        if code != 0 or not python.is_file():
            raise InstallError(
                "创建 Python 虚拟环境失败。",
                hint=(
                    "Debian/Ubuntu 需要先执行 sudo apt install python3-venv；"
                    "Windows 请重新安装 Python 3.10+ 并勾选 pip 与 tcl/tk 选项。"
                ),
                detail=_tail(lines),
            )

    if not _probe(python, "import pip"):
        _emit(progress, "初始化 pip …")
        code, lines = _run(
            [str(python), "-m", "ensurepip", "--upgrade"],
            progress,
            timeout=VENV_TIMEOUT,
        )
        if code != 0:
            raise InstallError(
                "虚拟环境里没有可用的 pip。",
                hint="请删除目录 %s 后重试，或重新安装 Python。" % target,
                detail=_tail(lines),
            )

    if upgrade_pip:
        _emit(progress, "更新 pip …")
        code, lines = _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--upgrade",
                "--disable-pip-version-check",
                "pip",
            ],
            progress,
            timeout=PIP_TIMEOUT,
        )
        if code != 0:
            _emit(progress, "警告：pip 自更新失败，继续安装依赖")

    specs = _requirement_specs(requirements_file)
    if requirements_file is not None and Path(requirements_file).is_file():
        install_args = ["-r", str(Path(requirements_file).expanduser())]
    else:
        install_args = specs
    _emit(progress, "安装 Python 依赖：" + ", ".join(specs))
    code, lines = _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            *install_args,
        ],
        progress,
        timeout=PIP_TIMEOUT,
    )
    if code != 0:
        raise InstallError(
            "Python 依赖安装失败（mcp / websockets）。",
            hint=(
                "常见原因是网络访问不了 PyPI：可设置国内镜像后重试，例如 "
                "set PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple（Windows）"
                "或 export PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple（macOS/Linux）。"
            ),
            detail=_tail(lines),
        )

    if not _has_requirements(python):
        raise InstallError(
            "依赖安装完成，但虚拟环境仍无法导入 mcp。",
            hint="请删除目录 %s，然后重新安装。" % target,
            detail=_tail(lines),
        )

    return {
        "python": str(python),
        "venv_dir": str(target),
        "packages": _installed_packages(python),
        "created": True,
        "installed": True,
    }


__all__ = [
    "DEFAULT_VENV_DIR",
    "INTERPRETER_TIMEOUT",
    "LAUNCHER_NAMES",
    "LAUNCHER_VERSION_ARGS",
    "OUTPUT_TAIL",
    "REQUIRED_PACKAGES",
    "base_python",
    "base_python_command",
    "interpreter_candidates",
    "is_launcher",
    "launcher_candidates",
    "looks_like_python",
    "normalize_path",
    "path_basename",
    "resolve_interpreter",
    "setup_venv",
    "venv_python",
]
