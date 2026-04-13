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
- **内置配置面板** – 在 Godot 编辑器内直接修改端口并保存

### 系统要求

- **Godot** 4.6 或更高版本
- **OpenCode**（或任何支持本地 stdio MCP 的客户端）
- **Python** 3.10+
- **pip 包**：`mcp`、`websockets`

---

### 安装步骤

#### 1. 安装 Python 依赖

打开终端运行：

```bash
pip install mcp websockets