# Godot Bridge MCP

**让 AI 真正坐在 Godot 编辑器里：读场景、改节点、写脚本、撤销、运行游戏、看日志、截图、模拟输入。**

[![Protocol](https://img.shields.io/badge/protocol-2.0-blue)]() [![Godot](https://img.shields.io/badge/Godot-4.3%E2%80%934.7-478cbf)]() [![MCP SDK](https://img.shields.io/badge/MCP%20SDK-1.x%20%7C%202.x-green)]() [![License](https://img.shields.io/badge/license-MIT-lightgrey)]()

[中文](#中文) | [English](#english) · [工具参考](docs/tools.md) · [客户端配置](docs/clients.md) · [协议契约](PROTOCOL.md)

---

## 中文

### 为什么是它

| 差异点 | 说明 |
|---|---|
| **零配置** | 插件自动选端口、生成 token、写握手文件；Python 侧自动发现。8080 被占用也不用手改配置。 |
| **真·可撤销** | 每次写操作 = 一个 `EditorUndoRedoManager` action。一次 `Ctrl+Z` 完整回滚一次 AI 调用，覆盖节点、属性、脚本、文件。 |
| **编辑期 + 运行期** | 同一插件覆盖「改场景」与「跑起来验证」：运行/停止、日志、截图、键鼠与 Input action 模拟、游戏内节点树。 |
| **类型不丢** | Vector2/3/4、Color、Transform2D/3D、Basis、Quaternion、Plane、AABB、NodePath、资源引用双向编解码，不再出现 `"Vector2(1, 2)"` 这种不可解析字符串。 |
| **跨客户端** | Claude Code / Cursor / Codex / OpenCode / Cline 一键复制配置，任何支持 stdio 的 MCP 客户端都能用。 |
| **MCP SDK 双兼容** | 同时支持 2026-07 之后的 `mcp 2.x`（`MCPServer`）与 `mcp 1.x`（`FastMCP`）。旧环境不再 ImportError。 |

### 与生态对比（2026-11 调研）

| 维度 | **本项目 v2.0** | Coding-Solo/godot-mcp | hi-godot/godot-ai | Godot MCP Pro |
|---|---|---|---|---|
| 工具数 | 74 | 14 | 46 | 187 |
| 编辑器内操作 | 场景/节点/属性/信号/脚本/资源全链路 | 文件级生成 | 编辑器 + 运行时 | 编辑器 + 运行时 |
| 运行时试玩 | 运行/日志/截图/输入/eval/游戏内树 | 运行 + 日志 | 有 | 有 |
| **每次调用可 Ctrl+Z** | **是（全部写工具）** | 否 | 部分 | 部分 |
| 零配置 | 自动端口 + token + 握手文件 | 需填 Godot 路径 | 需配 token | 需安装 |
| 鉴权 | 默认开启，可关闭 | 无 | 有 | 有 |
| MCP SDK 1.x / 2.x | 双兼容 | — | 2.x | 2.x |
| 价格 | 免费 MIT | 免费 | 免费 | 付费 |

> 协议层由官方 MCP Python SDK 2.3 驱动，遵循 **2026-07-28** 规范（无状态核心、结构化输出、
> 工具注解）；旧客户端走 1.x 兼容路径。

### 一键安装（图形界面，推荐新手）

不想碰命令行：从 [Releases](https://github.com/dgcrxs/Gohot_bridge_mcp/releases/latest) 下载对应平台的安装器，双击运行。

| 平台 | 文件 |
|---|---|
| Windows | `GodotBridgeMCP-Setup-2.0.0-windows.exe` |
| macOS | `GodotBridgeMCP-Setup-2.0.0-macos.zip`（解压后双击 `.app`） |
| Linux | `GodotBridgeMCP-Setup-2.0.0-linux`（`chmod +x` 后运行） |

安装器会替你完成四件事，全程不需要终端：

1. 把 `addons/godot_mcp` 复制进你选的 Godot 工程
2. 在 `project.godot` 里自动启用插件（保留原有其他插件，改前备份 `.bak-mcp`）
3. 在 `~/.godot-bridge-mcp/venv` 建独立 Python 环境并装依赖，不污染系统 Python
4. 按你选的客户端（Claude Code / Cursor / OpenCode / Codex / Cline）写配置文件

也支持把工程文件夹**直接拖到安装器图标上**，路径会自动预填。卸载用同一个安装器的「卸载插件」按钮。

> 安装器需要机器上有 Python 3.10+（用来建 venv）。没有的话请先用下面的手动方式。

### 30 秒接入

```bash
# 1) 装上 Python 依赖（插件本身零第三方依赖）
pip install "mcp>=1.30" "websockets>=12"

# 2) 把 addons/godot_mcp 复制进你的项目，然后：
#    Godot → 项目 → 项目设置 → 插件 → 启用 "Godot Bridge MCP"
#    底部面板出现 "MCP Bridge"，状态为绿色即已监听
```

客户端配置从 Dock 面板点 **Copy config** 直接复制，或参考 [docs/clients.md](docs/clients.md)。

> 分发时请同时保留 `server/` 目录（Python 服务端）；只复制 `addons/` 时，Dock 生成的配置会指向你本地仓库里的 `addons/godot_mcp/server.py`，仍需仓库存在。

### 功能矩阵

| 能力 | 能做什么 |
|---|---|
| 场景 | 读取树、当前场景、打开/新建/保存/另存、未保存标记 |
| 节点 | 增删改、重命名、移动父级、复制、按类型/组/脚本/名字查找、调用方法 |
| 属性 | 列出（含类型/usage）、读单/多、写单/多、重置为默认值 |
| 信号 | 列出（含连接）、连接、断开 |
| 脚本 | 读/写/建/删、语法检查、挂载/卸载、扫描、全项目搜索 |
| 资源与文件 | 列目录、资源信息/加载/创建/删除/依赖、读文件、写文件、重导入、搜索 |
| 编辑器 | 选区读写、撤销/重做/历史、定位 Inspector、打开脚本、项目设置、autoload |
| 运行时 | 运行/停止、游戏日志、截图、键鼠与 action 输入、游戏内 eval、游戏内节点树 |
| 文档 | 反射类的方法/属性/信号/枚举，按名搜索类 |
| 兼容 | 7 个 v1 工具名原样保留 |

完整 74 个工具的参数与返回见 [docs/tools.md](docs/tools.md)。

### Dock 面板

- 状态圆点：绿=已连接、黄=监听中无客户端、灰=停止
- 端口 + 自动启动开关 + 启动/停止/应用端口（冲突自动递增，写入 `project.godot`）
- Token 显示/复制（默认自动生成；置空可关闭鉴权）
- 客户端配置一键复制（5 种）
- 实时调用日志：方法、耗时、成功/失败、错误码，可清空
- 运行状态与错误提示（`last_error` 直接显示在状态栏）

### 工作方式

```
MCP Client ──stdio──> Python Server ──WebSocket 127.0.0.1──> Godot EditorPlugin
                                                              ├─ scene / node / prop / signal
                                                              ├─ script / res / file / fs
                                                              ├─ docs / execute
                                                              └─ runtime bridge ──> 游戏进程（autoload）
```

- 仅绑定 `127.0.0.1`；首帧鉴权，错误 token 直接 `close(4001)`。
- 编辑器端口默认 `8765`（冲突递增），运行时端口为编辑器端口 +1。
- 握手文件：`<项目>/.godot/mcp_bridge.json`（含端口、token、pid、项目路径、协议版本）。
- 写操作全部走 undo；`execute_script` 在隔离的临时节点里运行，返回可序列化结果。

### 常见问题

**连不上 / `cannot reach the Godot editor`**
1. Godot 底部面板是否显示绿色；没启动就点「启动」。
2. 端口是否被占用：Dock 会显示实际端口，握手文件在 `.godot/mcp_bridge.json`。
3. 客户端配置里的路径是否指向真实存在的 `addons/godot_mcp/server.py`（或已 `pip install -e .` 后用 `godot-bridge-mcp`）。
4. 手动验证：`python addons/godot_mcp/server.py --version`。

**`ImportError: cannot import name 'FastMCP'`**
已修复：服务端会先尝试 `mcp.server.MCPServer`（2.x），再回退 `mcp.server.fastmcp.FastMCP`（1.x）。若仍报错，`pip install -U "mcp>=1.30"`。

**截图返回 1008 `The game is rendering no frames`**
headless 或无渲染环境没有帧缓冲。用带窗口的 Godot 运行游戏后再截图。

**token 会被提交进 Git 吗**
会写在 `project.godot` 的 `[mcp_bridge] auth_token`。它只用于本机回环鉴权；如果不希望入库，
把该值改成空字符串即可关闭鉴权，或在团队内统一使用同一个 token。

**撤销没反应**
每个工具调用是一个 action；先确认操作确实成功（返回 `ok:true`），再 `godot_undo`。`node.call` 与 `execute_script` 的任意副作用无法自动生成反向操作。

### 开发与测试

```bash
pip install -e ".[dev]"
pytest -q                     # 129 个用例（含假 Godot 服务）
pytest -q -m port_conflict    # 端口冲突场景（会重启 Godot）
GODOT_BIN=/path/to/godot pytest -q tests/test_godot_editor.py
```

真机集成测试需要一个已启用插件的工程；`tests/fixtures/project` 提供最小样例。

---

## English

**Godot Bridge MCP connects any MCP client to a live Godot 4 editor over a local WebSocket.**

- **Zero config** — auto port, auto token, handshake file, auto-discovery on the Python side.
- **Truly undoable** — every write tool is exactly one `EditorUndoRedoManager` action; one `Ctrl+Z` reverts one AI call.
- **Editor + runtime** — edit scenes, then run the game, read logs, take screenshots and simulate keyboard/mouse/actions.
- **Lossless values** — Vector/Color/Transform/Basis/Quaternion/Plane/AABB/NodePath/resources round-trip via `{"__gd": ...}`.
- **Any client** — Claude Code, Cursor, Codex, OpenCode, Cline; stdio transport.
- **SDK compatible** — works with MCP Python SDK 2.x (`MCPServer`) and 1.x (`FastMCP`).

### Install

```bash
pip install "mcp>=1.30" "websockets>=12"
# copy addons/godot_mcp into your project, enable the plugin in Project Settings → Plugins
```

Copy the client config from the dock panel, or see [docs/clients.md](docs/clients.md).

### Requirements

Godot 4.3 – 4.7 (4.7.2 tested), Python 3.10+, `mcp>=1.30`, `websockets>=12`.

### Documentation

[Tool reference](docs/tools.md) · [Client setup](docs/clients.md) · [Protocol contract](PROTOCOL.md) · [Changelog](CHANGELOG.md)

---

MIT License · 作者 / Author: [dgcrxs](https://github.com/dgcrxs)
