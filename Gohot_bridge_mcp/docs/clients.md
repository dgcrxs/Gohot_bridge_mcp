# 客户端配置 / Client Setup

服务端是一个标准 **stdio MCP server**，所以任何支持本地 stdio 的客户端都能接入。
把下面的 `<REPO>` 换成仓库的绝对路径；Windows 示例：`E:/tools/Gohot_bridge_mcp`。

> 推荐做法：先 `pip install -e <REPO>`，然后所有客户端统一用命令 `godot-bridge-mcp`，
> 这样不依赖仓库路径，也不会受 Python 启动目录影响。
> 不安装也可以，直接用 `python <REPO>/Gohot_bridge_mcp/addons/godot_mcp/server.py`。

---

## Claude Code

```bash
claude mcp add godot --scope user -- python <REPO>/Gohot_bridge_mcp/addons/godot_mcp/server.py
# 或安装后：
claude mcp add godot --scope user -- godot-bridge-mcp
```

验证：`claude mcp list` 应出现 `godot: connected`。

## Claude Desktop

`claude_desktop_config.json`（macOS：`~/Library/Application Support/Claude/`，
Windows：`%APPDATA%/Claude/`）：

```json
{
  "mcpServers": {
    "godot": {
      "command": "python",
      "args": ["<REPO>/Gohot_bridge_mcp/addons/godot_mcp/server.py"]
    }
  }
}
```

## Cursor

`~/.cursor/mcp.json`（全局）或项目内 `.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "godot": {
      "command": "python",
      "args": ["<REPO>/Gohot_bridge_mcp/addons/godot_mcp/server.py"],
      "env": {
        "GODOT_MCP_PROJECT": "<ABS_PATH_TO_GODOT_PROJECT>"
      }
    }
  }
}
```

`GODOT_MCP_PROJECT` 可省略——服务端会从当前目录向上查找 `.godot/mcp_bridge.json`。

## Codex

`~/.codex/config.toml`：

```toml
[mcp_servers.godot]
command = "python"
args = ["<REPO>/Gohot_bridge_mcp/addons/godot_mcp/server.py"]
# 可选：固定端口与 token
# env = { GODOT_MCP_PORT = "8765", GODOT_MCP_TOKEN = "..." }
```

## OpenCode

`opencode.json`（项目根）：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "godot": {
      "type": "local",
      "command": ["python", "<REPO>/Gohot_bridge_mcp/addons/godot_mcp/server.py"],
      "enabled": true
    }
  }
}
```

## Cline (VS Code)

`cline_mcp_settings.json`：

```json
{
  "mcpServers": {
    "godot": {
      "command": "python",
      "args": ["<REPO>/Gohot_bridge_mcp/addons/godot_mcp/server.py"],
      "disabled": false
    }
  }
}
```

---

## 环境变量

| 变量 | 作用 |
|---|---|
| `GODOT_MCP_PROJECT` | Godot 工程根目录（用于找 `.godot/mcp_bridge.json`） |
| `GODOT_MCP_PORT` | 强制端口，跳过自动发现 |
| `GODOT_MCP_TOKEN` | 强制 token（插件关闭鉴权时可留空） |
| `GODOT_MCP_HANDSHAKE` | 直接指定握手文件路径 |

优先级：显式参数 > 环境变量 > 向上查找握手文件 > 扫描 `8765–8784`。

## 多项目并行

每个 Godot 工程会写自己的 `.godot/mcp_bridge.json`，端口自动错开。
在客户端里为每个项目建一个 server 条目，并设置 `GODOT_MCP_PROJECT` 指向对应工程即可。

## 手动验证连接

```bash
python <REPO>/Gohot_bridge_mcp/addons/godot_mcp/server.py --version
python <REPO>/Gohot_bridge_mcp/addons/godot_mcp/server.py   # 直接运行，stdio 等待客户端
```

用 MCP Inspector：

```bash
npx @modelcontextprotocol/inspector python <REPO>/Gohot_bridge_mcp/addons/godot_mcp/server.py
```
