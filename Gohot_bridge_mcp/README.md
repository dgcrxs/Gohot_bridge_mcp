# godot-bridge-mcp (Python server)

Python side of **Godot Bridge MCP** — a stdio MCP server that drives a live Godot 4 editor
over a local WebSocket.

```bash
pip install -e .
godot-bridge-mcp --version
```

Configure your MCP client with `command = "godot-bridge-mcp"` (or
`python addons/godot_mcp/server.py`). The Godot addon must be enabled in the project.

- Protocol contract: [PROTOCOL.md](PROTOCOL.md)
- Client setup: [docs/clients.md](docs/clients.md)
- Tool reference: [docs/tools.md](docs/tools.md)
- Full documentation: [../README.md](../README.md)
