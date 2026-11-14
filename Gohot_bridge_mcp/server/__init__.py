"""Godot Bridge MCP server package (v2.0).

This package speaks the Godot Bridge Protocol v2.0 over WebSocket and exposes it
as Model Context Protocol tools. It supports both MCP Python SDK 1.x
(``mcp.server.fastmcp.FastMCP``) and 2.x (``mcp.server.MCPServer``).
"""

__version__ = "2.0.0"
PROTOCOL_VERSION = "2.0"
SERVER_NAME = "godot-bridge"

__all__ = ["__version__", "PROTOCOL_VERSION", "SERVER_NAME"]
