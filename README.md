# Godot Bridge MCP

[English](#english) | [中文](#中文)

---
## 中文

### 简介

**Godot Bridge MCP** 是一个基于 **模型上下文协议（MCP）** 的插件，通过 WebSocket 将 [OpenCode](https://opencode.ai)（或其他 MCP 客户端）与 **Godot 4** 编辑器连接起来。  
它可以让你用自然语言（通过 AI）控制 Godot 编辑器：查询场景树、添加节点、修改属性、执行 GDScript 代码等，实现 AI 驱动的游戏开发自动化。

### 功能特性

- **AI 驱动开发** – 在 OpenCode 中直接用自然语言操控 Godot 编辑器
- **场景树操作** – 获取并修改整个场景结构
- **节点管理** – 动态添加、删除、重命名节点
- **属性控制** – 实时读写任意节点的属性
- **脚本执行** – 从 MCP 客户端运行 GDScript 代码片段
- **双向 WebSocket** – 持久化、低延迟的通信

### 系统要求

- **Godot** 4.6 或更高版本
- **OpenCode**（或任何支持本地 stdio MCP 的客户端）
- **Python** 3.10+
- **pip 包**：`mcp`、`websockets`

---

### 安装步骤（详细版）

#### 1. 安装 Python 依赖

打开终端运行：

```bash
pip install mcp websockets
```

#### 2. 将插件复制到 Godot 项目

把 `godot_bridge_mcp` 文件夹复制到你的 Godot 项目的 `addons/` 目录下：

```
your_godot_project/
└── addons/
    └── godot_bridge_mcp/
        ├── plugin.cfg
        ├── GodotBridgeWebSocket.gd
```

#### 3. 在 Godot 中启用插件

- 打开你的 Godot 项目
- 点击菜单 **项目 → 项目设置 → 插件**
- 找到 **"Godot Bridge MCP"**，状态改为 **启用**

启用后，编辑器控制台会显示：

```
MCP Bridge: WebSocket server started on port 8080
```

> 插件**必须在 Godot 编辑器运行时保持启用**，导出后的游戏中不会工作。

#### 4. 配置 MCP 客户端

**方式 A – 使用 `.mcp.json`（推荐）**

在项目根目录创建 `.mcp.json`：

```json
{
  "mcpServers": {
    "godot-bridge": {
      "command": "python",
      "args": ["E:/00/godot_mcp/server.py"],
      "env": {}
    }
  }
}
```

**方式 B – 使用 `opencode.jsonc`（旧版风格）**

在项目根目录创建 `opencode.jsonc`：

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "godot-bridge": {
      "type": "local",
      "command": ["python", "E:/00/godot_mcp/server.py"],
      "enabled": true
    }
  }
}
```

#### 5. 开始使用

- 保持 **Godot 编辑器打开**（且插件已启用）
- 在项目目录下运行 OpenCode：

```bash
cd your_godot_project
opencode
```

- 在 OpenCode 中，你可以让 AI 调用 MCP 工具，例如：`get_scene_tree`、`add_node` 等

---

### 验证连接

使用 `test_ws.py` 测试 WebSocket 连接：

```bash
python test_ws.py
```

如果成功，你会看到类似输出：

```
Connected!
Sent request
Received: {"id":1.0,"jsonrpc":"2.0","result":{...}}
```

---

### 可用的 MCP 工具

| 工具名 | 描述 | 参数 |
|--------|------|------|
| `get_scene_tree` | 获取当前场景的完整树结构 | 无 |
| `add_node` | 在场景中创建新节点 | `node_type`、`node_name`、`parent_path` |
| `get_node_properties` | 列出节点的所有属性 | `node_path` |
| `set_node_property` | 修改节点的属性值 | `node_path`、`property`、`value` |
| `execute_script` | 运行 GDScript 代码 | `code` |
| `get_selected_nodes` | 获取当前选中的节点 | 无 |
| `get_editor_info` | 获取编辑器信息 | 无 |
| `list_node_types` | 列出所有可用节点类型 | 无 |

---

### 配置（端口）

默认 WebSocket 地址为 `ws://localhost:8080`。

**Godot 插件** (`GodotBridgeWebSocket.gd`)：
```gdscript
var _port = 8080
```

**Python 服务器** (`server.py`)：
```python
GODOT_WS_URI = "ws://localhost:8080"
```

修改后需要**重启 Godot** 和 **OpenCode**。

---

### 常见问题

**Q: MCP server not found / 连接被拒绝**  
A:  
1. 确认 Godot 编辑器已打开且插件已启用
2. 检查 `.mcp.json` 中 `server.py` 的路径是否为**绝对路径**
3. 用 `test_ws.py` 测试 WebSocket

**Q: `ModuleNotFoundError: No module named 'mcp'`**  
A: 运行 `pip install mcp websockets`

**Q: 支持 Godot 3.x 吗？**  
A: 不支持，仅支持 Godot 4.x

---

### 许可证

MIT License © 2026

---

## English

### Overview

**Godot Bridge MCP** is a [Model Context Protocol (MCP)](https://modelcontextprotocol.io) plugin that connects [OpenCode](https://opencode.ai) (or any MCP‑compatible client) with the **Godot 4** editor via WebSocket.  
It enables AI‑driven game development automation – you can query scene trees, add nodes, modify properties, and execute GDScript code directly from your AI assistant.

### Features

- **AI‑Powered Development** – Control the Godot editor through natural language (via OpenCode)
- **Scene Tree Operations** – Get and modify the entire scene hierarchy
- **Node Management** – Dynamically add, remove, and rename nodes
- **Property Control** – Read/write any node property in real time
- **Script Execution** – Run GDScript snippets from your MCP client
- **Bidirectional WebSocket** – Persistent, low‑latency communication

### Requirements

- **Godot** 4.6 or later (4.x should work)
- **OpenCode** (or any MCP host that supports local stdio servers)
- **Python** 3.10+ (for the MCP server)
- **pip** packages: `mcp`, `websockets`

---

### Installation – Step by Step

#### 1. Install Python dependencies

Open a terminal (command prompt) and run:

```bash
pip install mcp websockets
```

> If you prefer a virtual environment, create one first, then install.

#### 2. Add the plugin to your Godot project

Copy the `godot_bridge_mcp` folder into your Godot project's `addons/` directory.  
Your project structure should look like this:

```
your_godot_project/
└── addons/
    └── godot_bridge_mcp/
        ├── plugin.cfg
        ├── GodotBridgeWebSocket.gd
        └── (other files, if any)
```

> If the `addons/` folder does not exist, create it manually.

#### 3. Enable the plugin in Godot

- Launch your Godot project.
- Go to **Project → Project Settings → Plugins**.
- Find **"Godot Bridge MCP"** in the list.
- Set its **Status** to `Enabled`.

After enabling, you should see a console message:

```
MCP Bridge: WebSocket server started on port 8080
```

> The plugin must be **enabled while the Godot editor is running** – it does not work in exported games.

#### 4. Configure your MCP client (OpenCode)

OpenCode uses an **MCP configuration file**. You have two options:

**Option A – Use `.mcp.json` (recommended for OpenCode 0.8+)**

Create a file named `.mcp.json` in your project root (or your home directory for global config):

```json
{
  "mcpServers": {
    "godot-bridge": {
      "command": "python",
      "args": ["E:/00/godot_mcp/server.py"],
      "env": {}
    }
  }
}
```

> Replace the `args` path with the actual location of `server.py` on your computer.

**Option B – Use `opencode.jsonc` (older style)**

Create `opencode.jsonc` in your project root:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "godot-bridge": {
      "type": "local",
      "command": ["python", "E:/00/godot_mcp/server.py"],
      "enabled": true
    }
  }
}
```

> Which one to use? `.mcp.json` is the modern MCP standard and works with many tools. `opencode.jsonc` is specific to older OpenCode versions. Try `.mcp.json` first.

#### 5. Start using the bridge

- Keep your **Godot editor open** (with the plugin enabled).
- Run OpenCode from your project directory:

```bash
cd your_godot_project
opencode
```

- In OpenCode, you can now ask your AI to call MCP tools like `get_scene_tree`, `add_node`, etc.

---

### Verifying the Connection

You can test the WebSocket connection independently using the provided `test_ws.py` script:

```bash
python test_ws.py
```

If successful, you will see:

```
Connected!
Sent request
Received: {"id":1.0,"jsonrpc":"2.0","result":{"has_scene":false,"scene_path":"","system":"Windows","version":"4.6.2-stable"}}
```

If it fails, ensure:
- Godot editor is running with the plugin enabled.
- No other application is using port `8080`.

---

### Available MCP Tools

| Tool | Description | Parameters |
|------|-------------|------------|
| `get_scene_tree` | Get the full scene tree as JSON | None |
| `add_node` | Create a new node in the scene | `node_type` (e.g., `"Node2D"`), `node_name`, `parent_path` (optional) |
| `get_node_properties` | List all properties of a node | `node_path` (e.g., `"/root/Main/Player"`) |
| `set_node_property` | Change a property value | `node_path`, `property`, `value` |
| `execute_script` | Run arbitrary GDScript code | `code` (string) |
| `get_selected_nodes` | Return nodes currently selected in the editor | None |
| `get_editor_info` | Get Godot version, OS, and scene info | None |
| `list_node_types` | Show all instantiable node types | None |

---

### Configuration (Port & Address)

By default, the WebSocket server listens on **`ws://localhost:8080`**.  
To change the port:

**In Godot plugin** (`GodotBridgeWebSocket.gd`):

```gdscript
var _port = 8080
```

**In Python server** (`server.py`):

```python
GODOT_WS_URI = "ws://localhost:8080"
```

After changing the port, **restart Godot** and **restart the MCP server** (restart OpenCode).

---

### Troubleshooting / FAQ

**Q: MCP server not found / connection refused**  
A:  
1. Make sure Godot editor is open and the plugin is **enabled** (check the console for "WebSocket server started").  
2. Verify the path to `server.py` in your `.mcp.json` is **absolute** and uses forward slashes (`/`).  
3. Test WebSocket with `test_ws.py` – if it fails, the Godot side is not listening.

**Q: `ModuleNotFoundError: No module named 'mcp'`**  
A: Run `pip install mcp websockets` again, and ensure you are using the same Python interpreter that OpenCode uses.

**Q: Can I use this with other MCP clients (Claude Desktop, Continue, etc.)?**  
A: Yes – as long as the client supports stdio MCP servers, you can point it to `server.py`.

**Q: Does it support Godot 3.x?**  
A: No, this plugin uses Godot 4's `WebSocketPeer` and `ClassDB` APIs. Godot 3 is not supported.

**Q: The plugin does not appear in the Plugins list**  
A: Ensure the folder structure is exactly `addons/godot_bridge_mcp/plugin.cfg`. The `plugin.cfg` file must contain the correct `script="GodotBridgeWebSocket.gd"` line.

---

### License

MIT License © 2026
---