# Godot Bridge MCP 一键安装器

给不想碰终端的用户准备的图形化安装器。双击运行，选一个 Godot 工程目录，点「安装」，剩下的全自动：

1. **插件就位** —— 把 `addons/godot_mcp` 复制进你的工程（覆盖旧版本，先删后拷，不留残留）。
2. **自动启用** —— 在 `project.godot` 的 `[editor_plugins]` 里加上本插件，保留你原有的其它插件，注释和换行风格都不动。
3. **依赖就位** —— 在 `~/.godot-bridge-mcp/venv` 建一个独立虚拟环境，装好 `mcp>=1.30.0` 与 `websockets>=12.0`，**不动系统 Python**。
4. **客户端就位** —— 按你选的 MCP 客户端写好配置文件，`godot` 服务器直接可用。

全程不需要打开终端，也不需要手改 `project.godot`。

---

## 一、普通用户：双击安装

| 平台 | 文件 | 说明 |
|---|---|---|
| Windows | `GodotBridgeMCP-Setup-<版本>-windows.exe` | 双击即可，不会弹黑框 |
| macOS | `GodotBridgeMCP-Setup-<版本>-macos.zip` | 解压后双击 `.app`；若提示「无法验证开发者」，右键 → 打开 |
| Linux | `GodotBridgeMCP-Setup-<版本>-linux` | `chmod +x` 后双击或运行 |

打开后的操作：

1. **第 1 步**：点「浏览…」，选中你的 Godot 工程目录（里面有 `project.godot` 的那个文件夹）。
   下方会显示两行工程摘要：加粗的工程名 + 右侧状态色标（<span>插件已启用</span> 绿 / 未启用 橙 / 插件文件缺失 红），
   第二行灰字是 `工程路径 · config_version=5`。也可以把工程文件夹直接拖到安装器图标上，启动即预填并校验。
2. **第 2 步**：保持「创建独立 Python 环境」勾选（推荐），在右侧选择你用的 MCP 客户端。
3. **第 3 步**：点蓝色的「安装」按钮。进度条铺满窗口底部并持续转动，日志逐行输出；完成后弹窗给出安装路径、配置路径和三步「下一步」清单。

右上角的胶囊文案始终反映真实状态：`未选择工程`（灰）→ `就绪`（蓝，校验通过）→ `安装中…`（橙）→ `已完成`（绿）/ `安装失败`（红）；
路径存在但不是 Godot 工程时显示 `工程无效`（红）。空闲时进度条只显示浅灰轨道与 `0%`，安装中切换为流动动画，成功后定格在 `100%`。

安装完成后：

1. 打开 Godot → 项目 → 项目设置 → 插件，确认 **Godot Bridge MCP** 已是启用状态（安装器已自动启用）。
2. 重启你的 MCP 客户端，即可看到名为 `godot` 的服务器。

> 安装器第一次动工程时会把 `project.godot` 备份成 `project.godot.bak-mcp`（只备份一次，不会覆盖你后来的改动）。
> 卸载请用界面上的「卸载插件」，它会移除插件目录并在项目设置里禁用。

---

## 二、客户端配置写到哪里

| 客户端 | 位置 | 安装器是否自动写入 |
|---|---|---|
| Claude Code | `<工程>/.mcp.json` | 是 |
| Cursor | `<工程>/.cursor/mcp.json` | 是 |
| OpenCode | `<工程>/opencode.json` | 是 |
| Codex CLI | `~/.codex/config.toml` | 否，只给出片段，复制粘贴 |
| Cline / VS Code | `cline_mcp_settings.json` | 否，只给出片段，复制粘贴 |

已存在的配置文件会**合并**：只替换 `godot` 这一项，你其它的 server 都会保留；旧文件备份为 `<文件名>.bak-mcp`（仅首次）。

所有客户端启动服务器的方式都一样：

```
<venv 里的 python>  <工程>/.godot-bridge-mcp/server/run_server.py
```

`run_server.py` 是安装器生成的启动器，它自己处理了 `sys.path`，所以**不需要设置 PYTHONPATH，也不需要 cwd**——这正是各种 MCP 客户端配置里没有 `cwd` 字段时仍然能跑起来的原因。

---

## 三、没有 exe 时用源码运行

仓库里已有 Python 3.10+ 时，可以直接跑源码（内部用 `python -m installer`）：

- Windows：双击 `installer/launch_windows.bat`
- macOS：双击 `installer/launch_macos.command`（首次可能需要 `chmod +x`，仓库里已设置可执行位）
- 任意平台手动：

```bash
python -m installer          # 打开图形界面
```

把工程文件夹拖到 `launch_windows.bat` / `launch_macos.command` 上也可以：脚本会把拖进来的路径原样透传（`%*` / `"$@"`），
安装器按 `--project` 处理，界面直接带着这个工程打开并完成校验。

---

## 四、命令行 / 无人值守安装

```bash
# 图形界面
python -m installer

# 图形界面 + 预填工程（启动后立即校验，但不会自动安装）
python -m installer --project /path/to/MyGame

# 同上，位置参数形式（拖动文件夹到图标时用的就是这种）
python -m installer /path/to/MyGame

# 无界面安装（CI、远程机器、没有 DISPLAY 的服务器）
python -m installer --headless --project /path/to/MyGame --client claude

# 机器可读输出（单行 JSON，便于脚本与 QA）
python -m installer --headless --project /path/to/MyGame --client claude --no-venv --json

# 卸载（移除插件 + 禁用，保留客户端配置和虚拟环境）
python -m installer --uninstall --project /path/to/MyGame
```

参数说明：

| 参数 | 作用 |
|---|---|
| `--headless` | 不打开窗口 |
| `--project PATH` | Godot 工程根目录（含 `project.godot`）；图形模式下表示「启动即预填」 |
| `PATH`（位置参数） | 等同 `--project`，方便拖拽启动 |
| `--client {claude,cursor,opencode,codex,cline}` | 目标 MCP 客户端，默认 `claude` |
| `--no-venv` | 跳过虚拟环境，用当前 Python（仅当你已经装好 `mcp` / `websockets`） |
| `--json` | 输出单行 JSON 结果 |
| `--uninstall` | 卸载插件 |
| `--version` | 打印版本 |

退出码：`0` 成功，`1` 安装失败，`2` 参数错误（例如没有图形界面却没加 `--headless`）。

`--json` 成功时形如：

```json
{"ok": true, "steps": [{"name": "validate", "status": "ok", "detail": "..."}], "python": "...", "client_config": {"client": "claude", "path": "...", "written": true, "snippet": "..."}}
```

失败时形如（退出码 1，不会抛 traceback）：

```json
{"ok": false, "error": "选中的目录不是 Godot 工程（缺少 project.godot）。", "hint": "请选择包含 project.godot 的工程根目录…", "steps": []}
```

---

## 五、自己打包 exe / app

```bash
python -m pip install "pyinstaller>=6.0"
pyinstaller installer/build.spec --noconfirm --clean
```

产物在 `dist/`：Windows 是 `GodotBridgeMCP-Setup.exe`（onefile + windowed，不弹控制台），
macOS 额外得到 `GodotBridgeMCP-Setup.app`。

`build.spec` 会把下面两个目录打进 `_payload/`，所以安装器离线也能工作：

```
Gohot_bridge_mcp/addons/godot_mcp  ->  _payload/addons/godot_mcp
Gohot_bridge_mcp/server            ->  _payload/server
```

Linux 上打包 tkinter 需要 `sudo apt-get install -y python3-tk`（CI 已包含，并装了 `xvfb` 用于冒烟测试）。
PyInstaller 还要求 Python 是带共享库（`--enable-shared`）编译的；用 pyenv 装的静态 Python 会报
*"Python was built without a shared library"*，换发行版自带的 `python3` 即可。

当前 Linux 产物约 12 MB（onefile，内含 `_payload/` 里的插件与 server，离线可用），
`< 40 MB`，因此 `build.spec` 的 `EXCLUDES` 不需要再加 `unittest` / `email` / `http` / `xml` / `pydoc` / `distutils`。

---

## 六、常见问题

**Q：提示「找不到系统 Python」，但机器上装了 Python？**
Windows 安装 Python 时没勾选 *Add python.exe to PATH*。重新安装勾选即可，或先手动 `python --version` 确认能在命令行里跑通。

**Q：pip 装依赖卡住 / 失败？**
多为访问 PyPI 受限。设置镜像后重试：

```bat
set PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
```

```bash
export PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
```

**Q：Godot 里看不到插件？**
确认工程里存在 `addons/godot_mcp/plugin.cfg`，然后在「项目 → 项目设置 → 插件」里看 Godot Bridge MCP 是否启用；必要时重启 Godot。

**Q：想彻底恢复原状？**
点「卸载插件」，再删除 `~/.godot-bridge-mcp/`，并删掉客户端配置里的 `godot` 项；`project.godot.bak-mcp` 是安装前的备份，可自行对比。

**Q：安装器会不会改我的业务代码？**
不会。它只动三处：`addons/godot_mcp/`、`project.godot` 的 `[editor_plugins]` 一行、`.godot-bridge-mcp/`，外加一个客户端配置文件。

---

## 七、代码结构

```
installer/
  __init__.py        常量（版本、路径、备份后缀）
  installer_core.py  编排与对外 API：校验 / 复制 / 启用 / 卸载 / run_install
  sources.py         定位插件与 server 源码（仓库布局 或 PyInstaller _payload）
  project_file.py    project.godot 的逐行解析与改写（保留注释与换行风格）
  server_runtime.py  部署 server 包并生成 run_server.py 启动器
  mcp_clients.py     五个客户端的配置模板、合并写入与备份
  venv_setup.py      venv 创建与 pip 安装（流式输出 + 尾部 15 行报错）
  gui.py             tkinter 单窗口界面（工作线程 + root.after 回主线程）
  theme.py           ttk 主题选择（原生优先，色彩不支持时回落 clam）、按钮/进度条样式、高 DPI
  __main__.py        入口：GUI / --project 预填 / --headless / --uninstall
  build.spec         PyInstaller onefile + windowed 规格
  launch_*.bat/.command  源码启动兜底脚本
```

约束：**只用标准库**（tkinter / urllib / subprocess / json / pathlib / zipfile / shutil），不引入任何第三方依赖；不修改 `Gohot_bridge_mcp/addons/**` 与 `Gohot_bridge_mcp/server/**` 的业务代码。
