# 构建产物 / Build artifacts — v2.0.0

## 推荐：图形化安装器

| 平台 | 文件 | 说明 |
|---|---|---|
| Windows | `GodotBridgeMCP-Setup-2.0.0-windows.exe` | 双击运行，无需终端、无需 Python 命令行 |
| Linux | `GodotBridgeMCP-Setup-2.0.0-linux` | `chmod +x` 后运行 |
| macOS | —（暂未提供 `.app`） | 用下面的「完整包 + 启动器」方式，见末节 |

> macOS 的 `.app` 需要在 macOS 上构建。当前请下载 `godot-bridge-mcp-2.0.0-full.zip`，解压后执行：
> `chmod +x installer/launch_macos.command && installer/launch_macos.command`
> （或在访达里右键 → 打开）。它会以源码方式启动同一个图形界面。

安装器流程：选择工程 → 复制 `addons/godot_mcp` → 自动启用插件（改前备份 `project.godot.bak-mcp`）→ 建独立 venv 装依赖 → 写入所选 MCP 客户端配置。支持把工程文件夹拖到图标上预填路径。

源码方式（无需打包）：`python -m installer`；命令行：`python -m installer --headless --project <工程> --client claude`。

## 手动安装用包

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
# Python 包
cd Gohot_bridge_mcp && python -m build

# 插件 zip
cd addons && zip -qr ../dist/godot-bridge-mcp-addon-2.0.0.zip godot_mcp

# 安装器（当前平台）
cd .. && pyinstaller installer/build.spec --noconfirm
```

三平台安装器由 `.github/workflows/release-installers.yml` 在 tag 推送或手动触发时构建并上传到 Release。

校验：

```bash
sha256sum -c SHA256SUMS.txt
```
