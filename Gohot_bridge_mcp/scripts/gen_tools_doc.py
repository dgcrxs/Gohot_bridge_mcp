"""Generate docs/tools.md from the live MCP server definition.

Usage:
    python scripts/gen_tools_doc.py > docs/tools.md
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp import Client  # noqa: E402

from server.server import create_server  # noqa: E402

GROUP_TITLES = {
    "system": "系统 / System",
    "scene": "场景 / Scene",
    "node": "节点 / Node",
    "property": "属性 / Property",
    "signal": "信号 / Signal",
    "script": "脚本 / Script",
    "resource": "资源与文件 / Resource & File",
    "runtime": "运行时试玩 / Runtime",
    "docs": "类文档 / Class docs",
    "legacy": "v1 兼容工具 / Legacy",
}

GROUP_ORDER = ["system", "scene", "node", "property", "signal", "script", "resource", "runtime", "docs", "legacy"]


GROUP_RULES = [
    ("legacy", ("",)),
    ("signal", ("signal",)),
    ("script", ("script",)),
    ("property", ("propert", "reset_prop")),
    ("resource", ("resource", "file", "directory", "search_files", "reimport")),
    ("runtime", ("run_scene", "stop_scene", "runtime", "screenshot", "input", "game_logs", "gdscript_in_game")),
    ("docs", ("class", "classes")),
    ("scene", ("scene", "open_scene", "save_scene", "create_scene")),
    ("node", ("node", "nodes", "add_node", "delete_node", "find_nodes", "call_node")),
]


def group_of(name: str) -> str:
    if not name.startswith("godot_"):
        return "legacy"
    body = name[len("godot_"):]
    for group, needles in GROUP_RULES:
        if group == "legacy":
            continue
        if any(needle in body for needle in needles):
            return group
    return "system"


def clean_description(text: str) -> str:
    lines = [line.rstrip() for line in (text or "").strip().splitlines()]
    out: List[str] = []
    for line in lines:
        if line.strip().lower().startswith("args:"):
            break
        if line.strip().lower().startswith("returns:"):
            break
        out.append(line)
    return " ".join(part.strip() for part in out if part.strip())


def schema_summary(schema: Dict[str, Any]) -> str:
    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    required = set(schema.get("required", []) if isinstance(schema, dict) else [])
    if not props:
        return "—"
    cells = []
    for key, spec in props.items():
        type_name = spec.get("type", "any")
        if type_name == "array":
            item = spec.get("items", {}).get("type", "any")
            type_name = f"{item}[]"
        marker = "" if key in required else "?"
        default = spec.get("default")
        detail = f"`{key}{marker}`: {type_name}"
        if default not in (None, "", []):
            detail += f" = `{default}`"
        cells.append(detail)
    return "<br>".join(cells)


async def main() -> None:
    server = create_server()
    async with Client(server) as client:
        tools = (await client.list_tools()).tools

    buckets: Dict[str, List[Any]] = {key: [] for key in GROUP_ORDER}
    for item in sorted(tools, key=lambda t: t.name):
        buckets.setdefault(group_of(item.name), []).append(item)

    print("# 工具参考 / Tool Reference\n")
    print(f"共 **{len(tools)}** 个工具。所有工具返回 dict；失败时返回")
    print("`{\"ok\": false, \"error\": {\"code\", \"message\", \"hint\", \"context\"}}`，")
    print("错误码见 [PROTOCOL.md](../PROTOCOL.md#5-error-codes)。\n")
    print("> 本文件由 `scripts/gen_tools_doc.py` 从服务端定义自动生成，请勿手工编辑。\n")

    for key in GROUP_ORDER:
        items = buckets.get(key, [])
        if not items:
            continue
        print(f"## {GROUP_TITLES[key]}\n")
        print("| 工具 | 说明 | 参数 |")
        print("|---|---|---|")
        for item in items:
            desc = clean_description(item.description or "").replace("|", "\\|")
            params = schema_summary(getattr(item, "input_schema", None) or getattr(item, "inputSchema", {}) or {}).replace("|", "\\|")
            print(f"| `{item.name}` | {desc} | {params} |")
        print()

    read_only = sum(1 for t in tools if getattr(getattr(t, "annotations", None), "readOnlyHint", False))
    print("## 注解统计\n")
    print(f"- 只读工具：{read_only} / {len(tools)}")
    print(f"- 写工具（会创建 undo action）：{len(tools) - read_only}")


if __name__ == "__main__":
    asyncio.run(main())
