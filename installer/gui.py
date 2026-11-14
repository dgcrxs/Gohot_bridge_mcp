"""Tkinter front-end for the Godot Bridge MCP installer.

Single window, standard library only. Every long running action runs in a
worker thread; all widget access happens on the Tk main thread through
``root.after(0, ...)``.

Layout (top to bottom)::

    header          product name + live status
    step 1          project picker + two line project summary
    step 2          options (venv + MCP client)
    actions         primary「安装」 + secondary「卸载插件」「打开工程目录」
    log             timestamped progress lines
    bottom          full width progress bar + percentage + one line status

The header status is a small state machine (``STATE_*``): ``未选择工程`` at
startup, ``就绪`` once the project validates, ``安装中…`` while the worker
runs, then ``已完成`` or ``安装失败``. ``工程无效`` covers a path that exists
but is not a Godot project. Only :meth:`InstallerApp._set_state` writes the
label, so no branch can leave stale text behind.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import tkinter as tk
import traceback
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, scrolledtext, ttk
from typing import Any, Dict, Optional

from . import ADDON_RELPATH, APP_NAME, VERSION
from .installer_core import InstallError, run_install, uninstall, validate_project
from .mcp_clients import CLIENTS, DEFAULT_CLIENT, client_keys, label_map
from .theme import PROGRESS_STYLE, apply_theme, enable_high_dpi

WINDOW_WIDTH = 780
WINDOW_HEIGHT = 672
MIN_WIDTH = 748
MIN_HEIGHT = 620
PAD_X = 16
PAD_Y = 6

#: Minimum width of the primary button, in characters.
INSTALL_BUTTON_WIDTH = 26
#: Gap between the primary button and the secondary button group, in pixels.
ACTION_GAP = 24

COLOR_MUTED = "#6b7280"
COLOR_FAILED = "#b91c1c"
COLOR_BADGE_ENABLED = "#15803d"
COLOR_BADGE_DISABLED = "#c2410c"
COLOR_BADGE_MISSING = "#b91c1c"

#: Header palette of the installer states.
COLOR_STATE_IDLE = "#64748b"
COLOR_STATE_READY = "#2563eb"
COLOR_STATE_RUNNING = "#c2410c"
COLOR_STATE_DONE = "#15803d"
COLOR_STATE_FAILED = "#b91c1c"

#: Installer states. Every transition goes through ``InstallerApp._set_state``.
STATE_NO_PROJECT = "no_project"
STATE_READY = "ready"
STATE_INVALID = "invalid"
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_FAILED = "failed"

#: Header text of each installer state.
STATE_TEXT = {
    STATE_NO_PROJECT: "未选择工程",
    STATE_READY: "就绪",
    STATE_INVALID: "工程无效",
    STATE_RUNNING: "安装中…",
    STATE_DONE: "已完成",
    STATE_FAILED: "安装失败",
}

#: Header colour of each installer state.
STATE_COLOR = {
    STATE_NO_PROJECT: COLOR_STATE_IDLE,
    STATE_READY: COLOR_STATE_READY,
    STATE_INVALID: COLOR_STATE_FAILED,
    STATE_RUNNING: COLOR_STATE_RUNNING,
    STATE_DONE: COLOR_STATE_DONE,
    STATE_FAILED: COLOR_STATE_FAILED,
}

#: Percentage hint shown next to the bar while it runs indeterminate.
PROGRESS_UNKNOWN_TEXT = "…"

#: Badge shown next to the project name, keyed by installation state.
BADGE_ENABLED = "插件已启用"
BADGE_DISABLED = "未启用"
BADGE_MISSING = "插件文件缺失"

#: First line of the log view: what the user is expected to do.
LOG_GREETING = "选择工程后点「安装」，约 1–3 分钟（首次需下载 Python 依赖）。"

#: Placeholder of the project summary while nothing is selected.
INFO_PLACEHOLDER = "请选择包含 project.godot 的 Godot 工程目录"


class InstallerApp:
    """The single installer window."""

    def __init__(self, root: tk.Tk, initial_project: Optional[str] = None) -> None:
        self.root = root
        self.project_var = tk.StringVar(master=root, value="")
        self.client_var = tk.StringVar(master=root, value=str(CLIENTS[DEFAULT_CLIENT]["label"]))
        self.make_venv_var = tk.BooleanVar(master=root, value=True)
        self.status_var = tk.StringVar(master=root, value=STATE_TEXT[STATE_NO_PROJECT])
        self.info_var = tk.StringVar(master=root, value=INFO_PLACEHOLDER)
        self.info_detail_var = tk.StringVar(master=root, value="")
        self.statusbar_var = tk.StringVar(master=root, value="请选择工程目录后开始安装")
        self.progress_var = tk.StringVar(master=root, value="0%")
        self.state = STATE_NO_PROJECT
        self.busy = False
        self._closing = False
        self._mono_font = tkfont.nametofont("TkFixedFont").copy()
        self._mono_font.configure(size=9)
        self._build()
        self._set_state(STATE_NO_PROJECT)
        if initial_project:
            self.load_project(initial_project)

    # ------------------------------------------------------------------ layout

    def _build(self) -> None:
        """Create every widget of the window."""
        root = self.root
        root.title("%s Installer %s" % (APP_NAME, VERSION))
        root.minsize(MIN_WIDTH, MIN_HEIGHT)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(4, weight=1)

        self._build_header()
        self._build_step_project()
        self._build_step_options()
        self._build_actions()
        self._build_log()
        self._build_bottom()

        self._log(LOG_GREETING)
        self._set_progress_idle()
        self._center_window()

    def _build_header(self) -> None:
        header = ttk.Frame(self.root)
        header.grid(row=0, column=0, sticky="ew", padx=PAD_X, pady=(14, 2))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text=APP_NAME, font=("", 16, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            header,
            text="Installer v%s" % VERSION,
            foreground=COLOR_MUTED,
            font=("", 9),
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.status_label = ttk.Label(
            header,
            textvariable=self.status_var,
            foreground=STATE_COLOR[self.state],
            font=("", 10, "bold"),
        )
        self.status_label.grid(row=0, column=1, rowspan=2, sticky="e")

    def _build_step_project(self) -> None:
        frame = ttk.LabelFrame(self.root, text="第 1 步  选择 Godot 工程")
        frame.grid(row=1, column=0, sticky="ew", padx=PAD_X, pady=PAD_Y)
        frame.columnconfigure(0, weight=1)

        self.path_entry = ttk.Entry(frame, textvariable=self.project_var, state="readonly")
        self.path_entry.grid(row=0, column=0, sticky="ew", padx=(10, 8), pady=(10, 6))
        self.browse_button = ttk.Button(frame, text="浏览…", width=10, command=self.on_browse)
        self.browse_button.grid(row=0, column=1, padx=(0, 10), pady=(10, 6))

        # Two line project summary: bold name + status badge, then path + format.
        info = ttk.Frame(frame)
        info.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 10))
        info.columnconfigure(0, weight=1)

        self.info_hint = ttk.Label(
            info, textvariable=self.info_var, foreground=COLOR_MUTED, font=("", 9)
        )
        self.info_hint.grid(row=0, column=0, sticky="w")

        self.info_name = ttk.Label(info, text="", font=("", 11, "bold"))
        self.info_name.grid(row=0, column=0, sticky="w")
        self.info_name.grid_remove()

        self.info_badge = tk.Label(
            info,
            text="",
            font=("", 9),
            foreground="#ffffff",
            background=COLOR_BADGE_DISABLED,
            padx=8,
            pady=1,
            borderwidth=0,
        )
        self.info_badge.grid(row=0, column=1, sticky="w", padx=(10, 0))
        self.info_badge.grid_remove()

        self.info_detail = ttk.Label(
            info, textvariable=self.info_detail_var, foreground=COLOR_MUTED, font=("", 9)
        )
        self.info_detail.grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 0))
        self.info_detail.grid_remove()

    def _build_step_options(self) -> None:
        frame = ttk.LabelFrame(self.root, text="第 2 步  安装选项")
        frame.grid(row=2, column=0, sticky="ew", padx=PAD_X, pady=PAD_Y)
        frame.columnconfigure(1, weight=1)

        self.venv_check = ttk.Checkbutton(
            frame,
            text="创建独立 Python 环境（推荐，不影响系统 Python）",
            variable=self.make_venv_var,
        )
        self.venv_check.grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(10, 4))

        ttk.Label(frame, text="MCP 客户端：").grid(
            row=1, column=0, sticky="w", padx=(10, 4), pady=(0, 10)
        )
        labels = [str(CLIENTS[key]["label"]) for key in client_keys()]
        self.client_menu = tk.OptionMenu(frame, self.client_var, *labels)
        self.client_menu.configure(width=22, anchor="w")
        self.client_menu.grid(row=1, column=1, sticky="w", pady=(0, 10))

    def _build_actions(self) -> None:
        frame = ttk.Frame(self.root)
        frame.grid(row=3, column=0, sticky="ew", padx=PAD_X, pady=(4, 2))
        # Column 3 is the elastic spacer: the buttons stay left aligned.
        frame.columnconfigure(3, weight=1)

        self.install_button = ttk.Button(
            frame,
            text="安装",
            width=INSTALL_BUTTON_WIDTH,
            style="Action.TButton",
            command=self.on_install,
        )
        self.install_button.grid(row=0, column=0, sticky="w")
        self.uninstall_button = ttk.Button(
            frame,
            text="卸载插件",
            width=14,
            style="Secondary.TButton",
            command=self.on_uninstall,
        )
        self.uninstall_button.grid(row=0, column=1, sticky="w", padx=(ACTION_GAP, 0))
        self.open_button = ttk.Button(
            frame,
            text="打开工程目录",
            width=14,
            style="Secondary.TButton",
            command=self.on_open_project,
        )
        self.open_button.grid(row=0, column=2, sticky="w", padx=(10, 0))

    def _build_log(self) -> None:
        frame = ttk.LabelFrame(self.root, text="日志")
        frame.grid(row=4, column=0, sticky="nsew", padx=PAD_X, pady=PAD_Y)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.log_widget = scrolledtext.ScrolledText(
            frame,
            height=9,
            wrap="word",
            state="disabled",
            font=self._mono_font,
            background="#f8fafc",
            relief="flat",
        )
        self.log_widget.grid(row=0, column=0, sticky="nsew", padx=(8, 4), pady=8)

    def _build_bottom(self) -> None:
        frame = ttk.Frame(self.root)
        frame.grid(row=5, column=0, sticky="ew", padx=PAD_X, pady=(0, 12))
        # A single elastic column: the bar spans the whole window width.
        frame.columnconfigure(0, weight=1)

        self.progress = ttk.Progressbar(
            frame,
            mode="determinate",
            maximum=100,
            value=0,
            style=PROGRESS_STYLE,
        )
        self.progress.grid(row=0, column=0, sticky="ew")
        # Percentage hint: keeps the bar readable when it sits at 0%.
        ttk.Label(
            frame,
            textvariable=self.progress_var,
            foreground=COLOR_MUTED,
            font=("", 9),
            width=5,
            anchor="e",
        ).grid(row=0, column=1, sticky="e", padx=(8, 0))
        ttk.Label(frame, textvariable=self.statusbar_var, foreground=COLOR_MUTED).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )

    # -------------------------------------------------------------- ui helpers

    def _center_window(self) -> None:
        """Centre the window on the screen once the layout has a real size."""
        root = self.root
        try:
            root.update_idletasks()
            screen_width = root.winfo_screenwidth()
            screen_height = root.winfo_screenheight()
            width = max(WINDOW_WIDTH, root.winfo_reqwidth())
            height = max(WINDOW_HEIGHT, root.winfo_reqheight())
            width = min(width, screen_width)
            height = min(height, screen_height)
            left = max(0, (screen_width - width) // 2)
            top = max(0, (screen_height - height) // 2)
            root.geometry("%dx%d+%d+%d" % (width, height, left, top))
        except tk.TclError:  # pragma: no cover - window destroyed during startup
            root.geometry("%dx%d" % (WINDOW_WIDTH, WINDOW_HEIGHT))

    def _post(self, func: Any, *args: Any) -> None:
        """Schedule ``func(*args)`` on the Tk main thread."""
        if self._closing:
            return
        try:
            self.root.after(0, lambda: func(*args))
        except tk.TclError:  # pragma: no cover - window already destroyed
            pass

    def _log(self, message: str) -> None:
        """Append one timestamped line to the log view (main thread only)."""
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", "[%s] %s\n" % (stamp, message))
        self.log_widget.configure(state="disabled")
        self.log_widget.see("end")

    def _log_threadsafe(self, message: str) -> None:
        """Progress callback usable from a worker thread."""
        self._post(self._log, message)

    def _set_state(self, kind: str) -> None:
        """Show one installer state in the header.

        This is the single place that touches the header label, so the visible
        text can never drift away from the real state.

        Args:
            kind: One of the ``STATE_*`` constants. Unknown values fall back to
                ``STATE_NO_PROJECT`` so the label never renders blank.
        """
        if kind not in STATE_TEXT:
            kind = STATE_NO_PROJECT
        self.state = kind
        self.status_var.set(STATE_TEXT[kind])
        try:
            self.status_label.configure(foreground=STATE_COLOR[kind])
        except tk.TclError:  # pragma: no cover - label destroyed during teardown
            pass

    def _set_progress_idle(self) -> None:
        """Show an empty but clearly visible trough at 0%."""
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=100, value=0)
        self.progress_var.set("0%")

    def _set_progress_running(self) -> None:
        """Animate the bar ("indeterminate") using the very same trough."""
        self.progress.configure(mode="indeterminate", maximum=100, value=0)
        self.progress_var.set(PROGRESS_UNKNOWN_TEXT)
        self.progress.start(15)

    def _set_progress_complete(self) -> None:
        """Pin the bar at 100% and leave it there (never falls back to 0)."""
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=100, value=100)
        self.progress_var.set("100%")

    def _set_busy(self, busy: bool) -> None:
        """Enable/disable the controls while a job is running."""
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.browse_button.configure(state=state)
        self.uninstall_button.configure(state=state)
        self.open_button.configure(state=state)
        self.install_button.configure(state=state, text="安装中…" if busy else "安装")
        self.venv_check.configure(state=state)
        try:
            self.client_menu.configure(state=state)
        except tk.TclError:  # pragma: no cover - menubutton teardown
            pass
        if busy:
            self._set_progress_running()
        else:
            self.progress.stop()

    def _selected_client(self) -> str:
        """Return the canonical client key chosen in the drop-down."""
        label = self.client_var.get()
        return label_map().get(label, DEFAULT_CLIENT)

    # ------------------------------------------------------------- info panel

    def _show_info_hint(self, text: str, color: str = COLOR_MUTED) -> None:
        """Show the single line placeholder (nothing selected / invalid)."""
        self.info_var.set(text)
        self.info_hint.configure(foreground=color)
        self.info_hint.grid()
        self.info_name.grid_remove()
        self.info_badge.grid_remove()
        self.info_detail.grid_remove()

    def _show_info_project(self, info: Dict[str, Any]) -> None:
        """Show the two line summary of a valid Godot project."""
        if not info.get("addon_present"):
            badge, badge_color = BADGE_MISSING, COLOR_BADGE_MISSING
        elif info.get("plugin_enabled"):
            badge, badge_color = BADGE_ENABLED, COLOR_BADGE_ENABLED
        else:
            badge, badge_color = BADGE_DISABLED, COLOR_BADGE_DISABLED

        self.info_hint.grid_remove()
        self.info_name.configure(text=str(info.get("name") or ""))
        self.info_name.grid()
        self.info_badge.configure(text=badge, background=badge_color)
        self.info_badge.grid()

        version = info.get("config_version") or 0
        detail = str(info.get("path") or "")
        if version:
            detail = "%s · config_version=%s" % (detail, version)
        self.info_detail_var.set(detail)
        self.info_detail.grid()

    def _refresh_info(self) -> Optional[Dict[str, Any]]:
        """Validate the selected directory, refresh the info panel and the state.

        Returns:
            The project summary for a valid project, else ``None``. The header
            state is set here as well: ``STATE_NO_PROJECT`` for an empty
            selection, ``STATE_INVALID`` when the path is not a Godot project
            and ``STATE_READY`` once validation succeeds.
        """
        raw = self.project_var.get().strip()
        if not raw:
            self._show_info_hint(INFO_PLACEHOLDER)
            self._set_state(STATE_NO_PROJECT)
            return None
        try:
            info = validate_project(Path(raw))
        except InstallError as error:
            self._show_info_hint(error.user_message, COLOR_FAILED)
            self._log("工程校验失败：%s" % error.user_message)
            self._set_state(STATE_INVALID)
            return None
        self._show_info_project(info)
        self._set_state(STATE_READY)
        return info

    # ----------------------------------------------------------------- actions

    def load_project(self, path: str) -> Optional[Dict[str, Any]]:
        """Prefill the project entry from ``--project`` and validate it.

        The project is only inspected, never installed. Used by the GUI entry
        point (and by the regression scripts driving ``InstallerApp``).
        """
        candidate = str(path or "").strip()
        if not candidate:
            return None
        expanded = str(Path(candidate).expanduser())
        self.project_var.set(expanded)
        self._log("已预填工程目录：%s" % expanded)
        info = self._refresh_info()
        if info is not None:
            self.statusbar_var.set("已识别工程：%s" % info["name"])
        else:
            self.statusbar_var.set("工程目录无效，请重新选择")
        return info

    def on_browse(self) -> None:
        """Let the user pick the Godot project directory."""
        if self.busy:
            return
        chosen = filedialog.askdirectory(title="选择 Godot 工程目录（包含 project.godot）", mustexist=True)
        if not chosen:
            return
        self.project_var.set(chosen)
        self._log("已选择工程目录：%s" % chosen)
        # A new project starts from an empty bar, never from a previous 100 %.
        self._set_progress_idle()
        self._refresh_info()

    def on_install(self) -> None:
        """Start the installation in a worker thread."""
        if self.busy:
            return
        raw = self.project_var.get().strip()
        if not raw:
            messagebox.showwarning("尚未选择工程", "请先点击「浏览…」选择包含 project.godot 的 Godot 工程目录。")
            return
        info = self._refresh_info()
        if info is None:
            messagebox.showerror("无法安装", "选中的目录不是有效的 Godot 工程，请重新选择。")
            return

        client = self._selected_client()
        make_venv = bool(self.make_venv_var.get())
        self._set_busy(True)
        self._set_state(STATE_RUNNING)
        self.statusbar_var.set("正在安装，请稍候…")
        self._log("开始安装：%s" % raw)
        self._log("客户端：%s · 独立虚拟环境：%s" % (CLIENTS[client]["label"], "是" if make_venv else "否"))
        worker = threading.Thread(
            target=self._install_worker, args=(raw, client, make_venv), daemon=True
        )
        worker.start()

    def _install_worker(self, project: str, client: str, make_venv: bool) -> None:
        """Run the installation (never touches widgets directly)."""
        try:
            result = run_install(
                Path(project),
                client=client,
                make_venv=make_venv,
                progress=self._log_threadsafe,
            )
        except InstallError as error:
            self._log_threadsafe("错误：%s" % error.user_message)
            if error.hint:
                self._log_threadsafe("提示：%s" % error.hint)
            for line in (error.detail or "").splitlines():
                self._log_threadsafe("  | %s" % line)
            self._post(self._install_failed, error.user_message, error.hint)
            return
        except Exception as error:  # noqa: BLE001 - defensive, keep UI alive
            self._log_threadsafe(traceback.format_exc().rstrip())
            self._post(self._install_failed, "未预期的错误：%s" % error, "请把日志区内容反馈给开发者。")
            return
        self._post(self._install_done, result)

    def _install_done(self, result: Dict[str, Any]) -> None:
        """Handle a successful installation (main thread)."""
        self._set_busy(False)
        self._refresh_info()
        self._set_progress_complete()
        self._set_state(STATE_DONE)
        self.statusbar_var.set("安装完成")

        config = result.get("client_config") or {}
        config_path = str(config.get("path") or "")
        client_label = str(config.get("label") or config.get("client") or "")
        project = str(result.get("project") or self.project_var.get().strip())
        addon_dir = str(result.get("addon_dir") or Path(project) / ADDON_RELPATH)

        self._log("安装完成。")
        if config_path:
            self._log("客户端配置：%s" % config_path)
        else:
            self._log("客户端 %s 需要手动粘贴配置片段。" % client_label)

        lines = [
            "插件已安装到：",
            addon_dir,
            "",
            "下一步：",
            "1. 打开 Godot，项目设置 → 插件，确认「Godot Bridge MCP」已启用",
            "2. 重启你的 MCP 客户端（%s）" % (client_label or "你的 MCP 客户端"),
            "3. 在对话里让它调用 godot_ping 验证连通",
            "",
        ]
        if config_path:
            lines.extend(["配置已写入：", config_path])
        else:
            lines.extend(
                [
                    "配置未自动写入，请手动粘贴以下片段：",
                    str(config.get("snippet", "")),
                ]
            )
        messagebox.showinfo("安装完成", "\n".join(lines))

    def _install_failed(self, message: str, hint: str) -> None:
        """Handle a failed installation (main thread)."""
        self._set_busy(False)
        self._set_progress_idle()
        self._set_state(STATE_FAILED)
        self.statusbar_var.set("安装失败")
        body = message
        if hint:
            body += "\n\n提示：" + hint
        body += "\n\n完整日志见窗口下方的日志区。"
        messagebox.showerror("安装失败", body)

    def on_uninstall(self) -> None:
        """Remove the plugin from the selected project."""
        if self.busy:
            return
        raw = self.project_var.get().strip()
        if not raw:
            messagebox.showwarning("尚未选择工程", "请先选择要卸载插件的 Godot 工程目录。")
            return
        if not messagebox.askyesno(
            "确认卸载",
            "将从工程中删除 addons/godot_mcp 与 .godot-bridge-mcp，并在项目设置里禁用插件。\n"
            "客户端配置和虚拟环境会保留。是否继续？",
        ):
            return
        self._set_busy(True)
        self._set_state(STATE_RUNNING)
        self.statusbar_var.set("正在卸载，请稍候…")
        self._log("开始卸载：%s" % raw)
        worker = threading.Thread(target=self._uninstall_worker, args=(raw,), daemon=True)
        worker.start()

    def _uninstall_worker(self, project: str) -> None:
        """Run the uninstall steps in a worker thread."""
        try:
            result = uninstall(Path(project), progress=self._log_threadsafe)
        except InstallError as error:
            self._log_threadsafe("错误：%s" % error.user_message)
            self._post(self._uninstall_failed, error.user_message, error.hint)
            return
        except Exception as error:  # noqa: BLE001 - defensive, keep UI alive
            self._log_threadsafe(traceback.format_exc().rstrip())
            self._post(self._uninstall_failed, "未预期的错误：%s" % error, "请把日志区内容反馈给开发者。")
            return
        self._post(self._uninstall_done, result)

    def _uninstall_done(self, result: Dict[str, Any]) -> None:
        """Handle a successful uninstall (main thread)."""
        self._set_busy(False)
        self._set_progress_idle()
        # The project is still selected: back to 就绪 (or 工程无效 / 未选择工程).
        self._refresh_info()
        self.statusbar_var.set("已卸载")
        messagebox.showinfo("卸载完成", "插件已从工程中移除并禁用。\n建议重启 Godot 编辑器。")

    def _uninstall_failed(self, message: str, hint: str) -> None:
        """Handle a failed uninstall (main thread)."""
        self._set_busy(False)
        self._set_progress_idle()
        self._set_state(STATE_FAILED)
        self.statusbar_var.set("卸载失败")
        body = message
        if hint:
            body += "\n\n提示：" + hint
        messagebox.showerror("卸载失败", body)

    def on_open_project(self) -> None:
        """Open the selected project folder in the system file manager."""
        raw = self.project_var.get().strip()
        if not raw:
            messagebox.showwarning("尚未选择工程", "请先选择 Godot 工程目录。")
            return
        path = Path(raw)
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as error:  # noqa: BLE001 - desktop integration varies
            messagebox.showerror("无法打开目录", "%s\n\n目录：%s" % (error, path))

    def on_close(self) -> None:
        """Handle the window close button."""
        if self.busy and not messagebox.askyesno(
            "安装正在进行", "安装还没有结束，现在关闭会中断安装。确定关闭吗？"
        ):
            return
        self._closing = True
        try:
            self.progress.stop()
        except tk.TclError:  # pragma: no cover - teardown race
            pass
        self.root.destroy()


def run_gui(initial_project: Optional[str] = None) -> int:
    """Create the window and run the Tk main loop. Returns the exit code.

    Args:
        initial_project: Optional project directory to prefill (``--project``).
    """
    enable_high_dpi()
    try:
        root = tk.Tk()
    except tk.TclError as error:
        print("错误：无法打开图形界面：%s" % error, file=sys.stderr)
        print("请改用命令行模式：python -m installer --headless --project <工程目录>", file=sys.stderr)
        return 2
    theme_name = apply_theme(root)
    app = InstallerApp(root, initial_project=initial_project)
    app._log("界面主题：%s" % theme_name)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    try:
        root.mainloop()
    except KeyboardInterrupt:  # pragma: no cover - Ctrl+C in a terminal
        app.on_close()
    return 0


__all__ = ["InstallerApp", "run_gui"]
