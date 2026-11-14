# Changelog

## Unreleased

### 新增

- **图形化安装器**（`installer/`）：选择 Godot 工程 → 复制插件 → 自动启用 →
  建独立 venv 装依赖 → 写入所选客户端配置。支持拖拽工程目录预填；三平台产物由
  `.github/workflows/release-installers.yml` 构建。
- 安装器命令行模式：`python -m installer --headless --project <工程> --client claude`
  与卸载 `--uninstall`，便于脚本化与 CI 验证。

## 2.0.0 — 2026-11-14

首个可用版本。v1 因 MCP Python SDK 2.x 将 `FastMCP` 改名为 `MCPServer` 而无法启动，
本次为完整重写，保留全部 v1 工具名与协议语义。

### 修复

- **无法启动**：`from mcp.server.fastmcp import FastMCP` 在 `mcp>=2.0` 下 ImportError。
  现同时兼容 `mcp.server.MCPServer`（2.x）与 `mcp.server.fastmcp.FastMCP`（1.x）。
- **属性类型损坏**：`JSON.stringify` 把 Vector/Color 等退化为不可解析字符串。
  新增双向值编解码（17 类），编解码失败返回 `1004`，不再静默写 0。
- **`execute_script` 失效**：多行代码缩进错误、动态脚本非 `@tool` 导致
  "placeholder instance"、返回值不可序列化。已重写包装器与返回值降级。
- **撤销无效**：v1 未接 `EditorUndoRedoManager`。v2 每个写工具 = 一个 undo action。
- **无保存/场景管理**：新增场景打开、新建、保存、另存、未保存标记。
- **端口硬编码**：改为自动探测（8765 起，冲突递增），并写入握手文件。

### 新增

- 74 个 MCP 工具，覆盖场景/节点/属性/信号/脚本/资源/文件/编辑器/运行时/文档。
- 运行时试玩：运行/停止、游戏日志、截图（PNG base64）、键鼠与 Input action 模拟、
  游戏内 `eval`、游戏内节点树。通过自动注册的 `McpRuntime` autoload 连接编辑器。
- 零配置：自动 token（`Crypto` 生成并持久化）、握手文件 `.godot/mcp_bridge.json`、
  Python 侧 env → 文件 → 端口扫描三级发现。
- 多客户端、首帧鉴权、空闲回收、请求日志环形缓冲。
- Dock 面板：状态灯、端口/自动启动、token 复制、5 种客户端配置一键复制、实时调用日志。
- 结构化错误：`code / message / hint / context / trace`，17 个错误码（见 `PROTOCOL.md`）。
- 协议 2.0：`group.action` 命名、legacy 别名、`get_capabilities` 能力发现。
- 测试：129 个用例，含假 Godot 服务、真机 headless 集成、撤销/重做与越界安全断言。

### 变更

- 插件入口由 `GodotBridgeWebSocket.gd` 改为 `plugin.gd`（旧文件保留但不加载）。
- Python 服务端拆分为 `server/` 包；`addons/godot_mcp/server.py` 保留为兼容入口。
- 最低 Godot 版本 4.3（`EditorInterface` 单例）。

### 已知限制

- `node.call` 与游戏内 `eval` 的副作用无法自动生成 undo 反向操作。
- headless 环境无法截图（返回 `1008` 并说明原因）。
- 运行时请求在编辑器主线程同步等待，`runtime.run` 最长阻塞 20 秒。
- 信号工具默认过滤编辑器内部连接（`@` 节点），可用 `include_internal` 打开。
- `undo_history` 依赖 `UndoRedo.get_action_name()`，部分 4.3/4.4 版本只暴露当前动作名。
