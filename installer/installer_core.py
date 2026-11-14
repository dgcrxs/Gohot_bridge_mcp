"""Core installation logic for the Godot Bridge MCP installer.

The module is deliberately GUI-free: every step is a plain function that can be
unit-tested or driven from ``python -m installer --headless``.

What the installer changes inside a Godot project::

    <project>/addons/godot_mcp/                 editor plugin (replaced)
    <project>/project.godot                     [editor_plugins] enabled=...
    <project>/.godot-bridge-mcp/server/         MCP server package + run_server.py
    <project>/.mcp.json (or client specific)    client configuration

Outside the project it only creates the shared virtualenv
(``~/.godot-bridge-mcp/venv``).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import ADDON_RELPATH, RUNTIME_DIR_REL, project_file as pf
from .mcp_clients import normalize_client, write_client_config
from .server_runtime import deploy_server
from .sources import PLUGIN_CFG_NAME, copy_tree, find_addon_source, find_server_source

ProgressCallback = Optional[Callable[[str], None]]

#: Godot project file and its one-shot backup.
PROJECT_FILE = pf.PROJECT_FILE
PROJECT_BACKUP = pf.PROJECT_BACKUP
#: ``res://`` path of the plugin descriptor written into ``project.godot``.
PLUGIN_RES_PATH = pf.PLUGIN_RES_PATH


# ----------------------------------------------------------------------- errors


class InstallError(Exception):
    """Installation failure with a user facing message and a fix hint.

    Attributes:
        user_message: One sentence (Chinese) describing what went wrong.
        hint: Actionable next step for the user, may be empty.
        detail: Optional raw output (for example the tail of pip output).
        steps: Steps that completed before the failure (filled by run_install).
    """

    def __init__(
        self,
        user_message: str,
        hint: str = "",
        *,
        detail: str = "",
    ) -> None:
        super().__init__(user_message)
        self.user_message = str(user_message)
        self.hint = str(hint)
        self.detail = str(detail)
        self.steps: List[Dict[str, str]] = []

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.user_message


# ---------------------------------------------------------------------- helpers


def _emit(progress: ProgressCallback, message: str) -> None:
    """Call ``progress`` when provided, swallowing callback failures."""
    if progress is None:
        return
    try:
        progress(message)
    except Exception:  # noqa: BLE001 - UI callbacks must not break the install
        pass


def _project_dir(project: Path) -> Path:
    """Resolve ``project`` and make sure it looks like a Godot project."""
    path = Path(project).expanduser()
    try:
        resolved = path.resolve()
    except OSError:  # pragma: no cover - defensive
        resolved = path.absolute()
    if not resolved.exists():
        raise InstallError(
            "工程目录不存在：%s" % resolved,
            hint="请重新选择 Godot 工程目录（里面应有 project.godot 文件）。",
        )
    if not resolved.is_dir():
        raise InstallError(
            "这不是一个目录：%s" % resolved,
            hint="请选择 Godot 工程的根目录。",
        )
    if not (resolved / PROJECT_FILE).is_file():
        raise InstallError(
            "选中的目录不是 Godot 工程（缺少 project.godot）。",
            hint="请选择包含 project.godot 的工程根目录，通常是 Godot 里「项目 → 打开」时选择的那个文件夹。",
        )
    return resolved


# ------------------------------------------------------------------ public API


def validate_project(path: Path) -> Dict[str, Any]:
    """Validate that ``path`` is a Godot project and report its state.

    Args:
        path: Candidate project directory.
    Returns:
        ``{"name", "path", "plugin_enabled", "addon_present",
        "godot_version_hint", "config_version"}``
    Raises:
        InstallError: When the directory does not exist or has no ``project.godot``.
    """
    project = _project_dir(path)
    text = pf.read_text(project / PROJECT_FILE)
    enabled = pf.editor_plugins_enabled(text)
    return {
        "name": pf.project_name(text, project.name),
        "path": str(project),
        "plugin_enabled": PLUGIN_RES_PATH in enabled,
        "addon_present": (project / ADDON_RELPATH / PLUGIN_CFG_NAME).is_file(),
        "godot_version_hint": pf.guess_version(text),
        "config_version": pf.config_version(text),
    }


def install_addon(
    project: Path,
    source: Optional[Path] = None,
    *,
    backup: bool = True,
) -> Dict[str, Any]:
    """Copy the editor plugin into ``project`` (replacing older versions).

    The previous ``addons/godot_mcp`` directory is removed before copying so no
    stale file survives an upgrade. ``project.godot`` is backed up to
    ``project.godot.bak-mcp`` the first time the installer touches the project.

    Args:
        project: Godot project root.
        source: Addon directory, discovered automatically when omitted.
        backup: Create ``project.godot.bak-mcp`` when it does not exist yet.
    Returns:
        ``{"copied", "addon_dir", "backup", "removed_previous", "source"}``
    Raises:
        InstallError: When the project or the addon source is invalid.
    """
    project_dir = _project_dir(project)
    addon_source = Path(source).expanduser() if source is not None else find_addon_source()
    if not (addon_source / PLUGIN_CFG_NAME).is_file():
        raise InstallError(
            "插件源目录无效：%s 里没有 plugin.cfg。" % addon_source,
            hint="请重新下载安装包，或确认插件源指向 addons/godot_mcp。",
        )

    backup_path = project_dir / PROJECT_BACKUP
    backup_created = ""
    if backup and not backup_path.exists():
        backup_path.write_bytes((project_dir / PROJECT_FILE).read_bytes())
        backup_created = str(backup_path)

    target = project_dir / ADDON_RELPATH
    removed_previous = target.exists()
    if removed_previous:
        shutil.rmtree(str(target))
    copied = copy_tree(addon_source, target)
    return {
        "copied": copied,
        "addon_dir": str(target),
        "backup": backup_created,
        "removed_previous": removed_previous,
        "source": str(addon_source),
    }


def install_server_runtime(project: Path, source: Optional[Path] = None) -> Dict[str, Any]:
    """Copy the MCP server package and generate ``run_server.py``.

    Args:
        project: Godot project root.
        source: Addon source directory (used to derive the repository layout).
    Returns:
        ``{"server_dir", "run_script", "server_source", "copied"}``
    Raises:
        InstallError: When the project or the server package cannot be found.
    """
    project_dir = _project_dir(project)
    return deploy_server(project_dir, source)


def enable_plugin(project: Path) -> Dict[str, Any]:
    """Enable the plugin in ``[editor_plugins]`` (idempotent).

    Args:
        project: Godot project root.
    Returns:
        ``{"changed", "enabled"}`` where ``enabled`` is the resulting list.
    Raises:
        InstallError: When the project is invalid.
    """
    project_file = _project_dir(project) / PROJECT_FILE
    text = pf.read_text(project_file)
    enabled = pf.editor_plugins_enabled(text)
    if PLUGIN_RES_PATH in enabled:
        return {"changed": False, "enabled": enabled}
    updated = list(enabled) + [PLUGIN_RES_PATH]
    pf.write_text(project_file, pf.set_enabled_items(text, updated))
    return {"changed": True, "enabled": updated}


def disable_plugin(project: Path) -> Dict[str, Any]:
    """Remove the plugin from ``[editor_plugins]`` (idempotent).

    Args:
        project: Godot project root.
    Returns:
        ``{"changed", "enabled"}`` where ``enabled`` is the resulting list.
    Raises:
        InstallError: When the project is invalid.
    """
    project_file = _project_dir(project) / PROJECT_FILE
    text = pf.read_text(project_file)
    enabled = pf.editor_plugins_enabled(text)
    if PLUGIN_RES_PATH not in enabled:
        return {"changed": False, "enabled": enabled}
    remaining = [item for item in enabled if item != PLUGIN_RES_PATH]
    pf.write_text(project_file, pf.set_enabled_items(text, remaining))
    return {"changed": True, "enabled": remaining}


def remove_addon(project: Path, *, runtime: bool = True) -> Dict[str, Any]:
    """Delete the copied plugin (and by default the server runtime).

    Args:
        project: Godot project root.
        runtime: Also delete ``.godot-bridge-mcp``.
    Returns:
        ``{"removed_addon", "removed_runtime", "addon_dir", "runtime_dir"}``
    Raises:
        InstallError: When the project is invalid.
    """
    project_dir = _project_dir(project)
    addon_dir = project_dir / ADDON_RELPATH
    removed_addon = addon_dir.exists()
    if removed_addon:
        shutil.rmtree(str(addon_dir))
    runtime_dir = project_dir / RUNTIME_DIR_REL
    removed_runtime = False
    if runtime and runtime_dir.exists():
        shutil.rmtree(str(runtime_dir))
        removed_runtime = True
    return {
        "removed_addon": removed_addon,
        "removed_runtime": removed_runtime,
        "addon_dir": str(addon_dir),
        "runtime_dir": str(runtime_dir),
    }


def setup_venv(
    *,
    venv_dir: Optional[Path] = None,
    progress: ProgressCallback = None,
) -> Dict[str, Any]:
    """Create the isolated venv and install the Python dependencies.

    Thin wrapper around :func:`installer.venv_setup.setup_venv`; imported lazily
    so the import graph stays acyclic.

    Args:
        venv_dir: Target directory, defaults to ``~/.godot-bridge-mcp/venv``.
        progress: Callback receiving human readable progress lines.
    Returns:
        ``{"python", "venv_dir", "packages", ...}``
    Raises:
        InstallError: When the environment cannot be prepared.
    """
    from .venv_setup import setup_venv as _setup_venv

    return _setup_venv(venv_dir=venv_dir, progress=progress)


def uninstall(project: Path, *, progress: ProgressCallback = None) -> Dict[str, Any]:
    """Undo the installation inside ``project``.

    Args:
        project: Godot project root.
        progress: Callback receiving human readable progress lines.
    Returns:
        ``{"ok", "steps", "project"}``
    Raises:
        InstallError: When the project is invalid.
    """
    project_dir = _project_dir(project)
    steps: List[Dict[str, str]] = []

    plugin = disable_plugin(project_dir)
    _emit(progress, "插件已从 project.godot 移除" if plugin["changed"] else "插件本就未启用")
    steps.append(
        {
            "name": "disable_plugin",
            "status": "ok",
            "detail": "changed" if plugin["changed"] else "already disabled",
        }
    )

    removal = remove_addon(project_dir)
    _emit(progress, "已删除插件目录" if removal["removed_addon"] else "插件目录不存在")
    steps.append(
        {
            "name": "remove_addon",
            "status": "ok",
            "detail": "removed" if removal["removed_addon"] else "already removed",
        }
    )
    steps.append(
        {
            "name": "remove_runtime",
            "status": "ok",
            "detail": "removed" if removal["removed_runtime"] else "already removed",
        }
    )
    _emit(progress, "卸载完成，客户端配置与虚拟环境已保留（可手动删除）")
    return {"ok": True, "steps": steps, "project": str(project_dir)}


def run_install(
    project: Path,
    *,
    client: str,
    make_venv: bool,
    progress: ProgressCallback = None,
) -> Dict[str, Any]:
    """Run the whole installation: validate, copy, enable, venv, client config.

    Args:
        project: Godot project root chosen by the user.
        client: MCP client key (``claude``/``cursor``/``opencode``/``codex``/``cline``).
        make_venv: Create the isolated virtualenv (recommended).
        progress: Callback receiving human readable progress lines.
    Returns:
        ``{"ok", "steps", "python", "client_config", ...}``
    Raises:
        InstallError: On the first failing step; ``exc.steps`` holds the log.
    """
    steps: List[Dict[str, str]] = []
    try:
        client_key = normalize_client(client)
    except ValueError as exc:
        raise InstallError(
            "不支持的 MCP 客户端：%s。" % client,
            hint="可选：claude、cursor、opencode、codex、cline。",
        ) from exc

    def _step(name: str, title: str, action: Callable[[], str]) -> str:
        """Run one step, recording its status; re-raises as InstallError."""
        _emit(progress, "步骤 %s：%s" % (name, title))
        try:
            detail = action()
        except InstallError as exc:
            steps.append({"name": name, "status": "failed", "detail": exc.user_message})
            exc.steps = steps
            raise
        except OSError as exc:
            message = "%s 失败：%s" % (title, exc)
            steps.append({"name": name, "status": "failed", "detail": message})
            error = InstallError(
                message,
                hint="请确认工程目录可写、Godot 编辑器未锁定 project.godot，然后重试。",
            )
            error.steps = steps
            raise error from exc
        steps.append({"name": name, "status": "ok", "detail": detail})
        return detail

    _emit(progress, "开始安装 %s…" % Path(project).expanduser())
    info = validate_project(project)
    project_dir = Path(info["path"])
    steps.append(
        {
            "name": "validate",
            "status": "ok",
            "detail": "Godot project: %s (%s)"
            % (info["name"], info["godot_version_hint"] or "version unknown"),
        }
    )
    _emit(progress, "工程校验通过：%s" % info["name"])

    state: Dict[str, Any] = {}

    def _addon() -> str:
        source = state.get("source") or find_addon_source()
        state["source"] = source
        result = install_addon(project_dir, source)
        _emit(progress, "插件已就位：%s（%d 个文件）" % (result["addon_dir"], result["copied"]))
        if result["backup"]:
            _emit(progress, "已备份 project.godot → %s" % result["backup"])
        return "%d files -> %s" % (result["copied"], result["addon_dir"])

    _step("addon", "复制插件文件", _addon)

    def _plugin() -> str:
        result = enable_plugin(project_dir)
        _emit(progress, "插件已在项目设置中启用" if result["changed"] else "插件此前已启用，跳过")
        joined = ", ".join(result["enabled"])
        return ("enabled -> " if result["changed"] else "already enabled -> ") + joined

    _step("plugin", "启用编辑器插件", _plugin)

    def _server() -> str:
        result = install_server_runtime(project_dir, state.get("source"))
        _emit(progress, "服务端运行时已就位：%s" % result["run_script"])
        return result["run_script"]

    run_script = _step("server", "部署 MCP 服务端", _server)

    python_exe = ""
    python_args: List[str] = []
    python_note = ""
    if make_venv:
        venv_state: Dict[str, Any] = {}

        def _venv() -> str:
            result = setup_venv(progress=progress)
            venv_state.update(result)
            _emit(progress, "Python 环境就绪：%s" % result["python"])
            return str(result["venv_dir"])

        _step("venv", "创建独立 Python 环境", _venv)
        python_exe = str(venv_state.get("python") or "")
    else:
        steps.append({"name": "venv", "status": "skipped", "detail": "使用当前 Python（--no-venv）"})
        _emit(progress, "跳过虚拟环境，使用当前 Python。")

    if not python_exe:
        # Imported lazily: venv_setup imports InstallError from this module.
        from .venv_setup import resolve_interpreter

        interpreter = resolve_interpreter()
        python_exe = str(interpreter["python"])
        python_args = [str(arg) for arg in interpreter["args"]]
        python_note = str(interpreter["note"])
        _emit(progress, "使用当前 Python：%s" % " ".join([python_exe, *python_args]))
        if python_note:
            _emit(progress, "注意：只能找到 py 启动器，配置里会写 py -3。")

    config_state: Dict[str, Any] = {}

    def _client() -> str:
        result = write_client_config(
            client_key,
            project_dir,
            python_exe,
            server_script=run_script,
            python_args=python_args,
            note=python_note,
        )
        config_state["config"] = result
        if result["written"]:
            _emit(progress, "已写入客户端配置：%s" % result["path"])
        else:
            _emit(progress, "该客户端需要手动粘贴配置片段（见安装完成后的提示）。")
        return result["path"] or "manual:" + str(result["label"])

    _step("client", "写入客户端配置", _client)

    _emit(progress, "安装完成。")
    return {
        "ok": True,
        "steps": steps,
        "python": python_exe,
        "python_args": python_args,
        "python_note": python_note,
        "client_config": config_state.get("config", {}),
        "project": str(project_dir),
        "project_name": info["name"],
        "addon_dir": str(project_dir / ADDON_RELPATH),
        "run_script": run_script,
    }


__all__ = [
    "InstallError",
    "PLUGIN_RES_PATH",
    "PROJECT_BACKUP",
    "PROJECT_FILE",
    "disable_plugin",
    "enable_plugin",
    "find_addon_source",
    "find_server_source",
    "install_addon",
    "install_server_runtime",
    "remove_addon",
    "run_install",
    "setup_venv",
    "uninstall",
    "validate_project",
    "write_client_config",
]
