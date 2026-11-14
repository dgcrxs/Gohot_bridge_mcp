"""Creation and population of the installer's isolated Python environment.

The venv lives outside the Godot project (``~/.godot-bridge-mcp/venv`` by
default) so it is shared between projects and can be deleted at any time
without touching the user's system Python.

Only the standard library is used.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

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


def base_python() -> str:
    """Return the interpreter used to *create* the venv.

    When frozen by PyInstaller ``sys.executable`` is the installer itself, so a
    real interpreter has to be discovered on ``PATH``.

    Raises:
        InstallError: If no usable interpreter can be found.
    """
    frozen = bool(getattr(sys, "frozen", False))
    if not frozen:
        return sys.executable or "python3"
    for name in ("python3", "python", "py"):
        found = shutil.which(name)
        if found:
            return found
    raise InstallError(
        "找不到系统 Python 解释器，无法创建虚拟环境。",
        hint=(
            "请先从 https://www.python.org/downloads/ 安装 Python 3.10 或更新版本，"
            "安装时勾选 Add python.exe to PATH，然后重新运行安装器。"
        ),
    )


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
        interpreter = base_python()
        code, lines = _run(
            [interpreter, "-m", "venv", str(target)],
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
    "OUTPUT_TAIL",
    "REQUIRED_PACKAGES",
    "base_python",
    "setup_venv",
    "venv_python",
]
