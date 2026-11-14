# 构建产物 / Build artifacts — v2.0.0

| 文件 | 用途 |
|---|---|
| `godot_bridge_mcp-2.0.0-py3-none-any.whl` | Python 服务端 wheel，`pip install` 后使用 `godot-bridge-mcp` 命令 |
| `godot_bridge_mcp-2.0.0.tar.gz` | 源码分发包（sdist） |
| `godot-bridge-mcp-addon-2.0.0.zip` | 仅 Godot 插件（解压后把 `addons/godot_mcp` 放进工程；服务端用 pip 安装） |
| `godot-bridge-mcp-2.0.0-full.zip` | 插件 + Python 服务端 + 文档的完整包（解压即用） |
| `SHA256SUMS.txt` | 校验和 |

## 安装

```bash
# 服务端（任选其一）
pip install godot_bridge_mcp-2.0.0-py3-none-any.whl
# 或从完整包运行
python Gohot_bridge_mcp/addons/godot_mcp/server.py
```

```text
# Godot 插件
解压 godot-bridge-mcp-addon-2.0.0.zip → 将 addons/godot_mcp 复制到工程 addons/
项目设置 → 插件 → 启用 Godot Bridge MCP
```

构建命令：

```bash
cd Gohot_bridge_mcp && python -m build
cd addons && zip -qr ../dist/godot-bridge-mcp-addon-2.0.0.zip godot_mcp
```

校验：

```bash
sha256sum -c SHA256SUMS.txt
```
