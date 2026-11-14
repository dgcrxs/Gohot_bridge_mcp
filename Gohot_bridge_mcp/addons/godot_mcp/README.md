# Godot Bridge MCP (addon)

这是 Godot 侧插件本体（纯 GDScript，零第三方依赖）。完整文档、客户端配置与工具参考见
仓库根目录的 [README](../../README.md)、[docs/clients.md](../../docs/clients.md)、
[docs/tools.md](../../docs/tools.md)。

## 安装

1. 将本目录（`addons/godot_mcp`）复制到你的 Godot 工程 `addons/` 下。
2. 项目 → 项目设置 → 插件 → 启用 **Godot Bridge MCP**。
3. 底部面板出现 **MCP Bridge**，状态灯变绿即监听成功（默认 `127.0.0.1:8765`）。
4. 在 MCP 客户端里使用 `server.py`（仓库内）或 `godot-bridge-mcp` 命令连接。

## 面板

- **Status**：绿=已连接 / 黄=监听中无客户端 / 灰=停止
- **Port**：修改后点 *Apply port*，会写入 `project.godot` 并重启服务
- **Token**：自动生成，可复制；清空并应用可关闭鉴权
- **Client config**：选择客户端后 *Copy config*，直接粘贴到对应配置文件
- **Log**：最近 500 次调用的方法、耗时、错误码

## 设置项

`project.godot` 中：

```ini
[mcp_bridge]
port=8765
auto_start=true
auth_token="..."
```

## 兼容性

- Godot 4.3 – 4.7（4.7.2 实测）
- 仅监听回环地址；默认首帧鉴权
- 写操作接入 `EditorUndoRedoManager`，一次工具调用 = 一次 `Ctrl+Z`
- 运行时试玩会自动注册 `McpRuntime` autoload（禁用插件时若由本插件添加则移除）
