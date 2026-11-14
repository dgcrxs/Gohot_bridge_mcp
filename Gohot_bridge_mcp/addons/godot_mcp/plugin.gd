@tool
extends EditorPlugin

## Godot Bridge MCP - editor entry point (v2.0).
##
## Responsibilities (PROTOCOL.md §1, §3.1):
##   * read `mcp_bridge/auto_start` and `mcp_bridge/port`
##   * ensure an auth token, probe free ports, start the WebSocket server and the
##     in-game runtime bridge
##   * register the `McpRuntime` autoload (only when missing, and removed again on
##     disable if this plugin added it)
##   * host the dock panel and forward every RPC log entry to the ws ring buffer
##
## Public surface used by the other modules:
##   ws, router, runtime, auto_start, port, runtime_port
##   get_status(), get_token(), get_undo(), log_event(entry)

const McpHandshake = preload("res://addons/godot_mcp/core/handshake.gd")
const McpWsServer = preload("res://addons/godot_mcp/core/ws_server.gd")
const McpRouter = preload("res://addons/godot_mcp/core/rpc_router.gd")

const PLUGIN_VERSION := "2.0.0"

## Provided by the other engineers; loaded defensively so a missing module can
## never prevent the transport layer from starting.
const DOCK_PANEL_PATHS := [
	"res://addons/godot_mcp/ui/dock_panel.gd",
	"res://addons/godot_mcp/core/dock_panel.gd",
]
const RUNTIME_OPS_PATHS := [
	"res://addons/godot_mcp/core/runtime_ops.gd",
	"res://addons/godot_mcp/ops/runtime_ops.gd",
]
const AUTOLOAD_SCRIPT_PATHS := [
	"res://addons/godot_mcp/runtime/mcp_runtime.gd",
	"res://addons/godot_mcp/core/mcp_runtime.gd",
]
const AUTOLOAD_KEY := "autoload/McpRuntime"

const SETTING_AUTO_START := "mcp_bridge/auto_start"
const SETTING_PORT := "mcp_bridge/port"
const DEFAULT_PORT := 8765
const PORT_SCAN_TRIES := 20
const DOCK_SLOT_NAME := "MCP"

## McpWsServer instance.
var ws: Variant = null
## McpRouter instance.
var router: Variant = null
## RuntimeOps instance (game process bridge), may be null when unavailable.
var runtime: Variant = null

var auto_start := true
var port := DEFAULT_PORT
var runtime_port := 0
var requests := 0
var errors := 0
var last_error := ""

var _dock: Control = null
var _added_autoload := false


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

func _enter_tree() -> void:
	_load_settings()
	var token := McpHandshake.ensure_token()
	_create_core(token)
	_start_transport()
	_register_autoload()
	_build_ui()
	set_process(true)
	print("[MCP] Godot Bridge MCP %s enabled (port %d, runtime port %d)" % [PLUGIN_VERSION, port, runtime_port])


func _exit_tree() -> void:
	set_process(false)
	_stop_runtime()
	if ws != null:
		ws.stop()
	McpHandshake.clear_handshake()
	_remove_ui()
	_unregister_autoload()
	ws = null
	router = null
	runtime = null
	print("[MCP] Godot Bridge MCP disabled")


func _process(_delta: float) -> void:
	if ws != null:
		ws.poll()
	if runtime != null and runtime.has_method("poll"):
		runtime.call("poll")


# ---------------------------------------------------------------------------
# Public API (used by other modules and the Python server)
# ---------------------------------------------------------------------------

## Snapshot of the plugin state (PROTOCOL.md §3.1 get_config / ping).
func get_status() -> Dictionary:
	var running := false
	var bound_port := port
	var clients := 0
	if ws != null:
		running = bool(ws.is_running())
		var active_port := int(ws.get("port"))
		if active_port > 0:
			bound_port = active_port
		clients = int(ws.client_count())
	return {
		"running": running,
		"port": bound_port,
		"runtime_port": runtime_port,
		"clients": clients,
		"token": get_token(),
		"auto_start": auto_start,
		"requests": requests,
		"errors": errors,
		"last_error": last_error,
		"has_runtime_bridge": _has_runtime_bridge(),
	}


## The shared auth token ("" means authentication is disabled).
func get_token() -> String:
	return McpHandshake.get_token()


## Undo manager used by every write RPC.
func get_undo() -> EditorUndoRedoManager:
	return get_undo_redo()


## Appends one RPC log entry to the transport ring buffer and updates counters.
func log_event(entry: Dictionary) -> void:
	var record := entry.duplicate(true)
	var method := String(record.get("method", ""))
	if not method.is_empty():
		requests += 1
		if not bool(record.get("ok", true)):
			errors += 1
			var message := String(record.get("error", ""))
			if not message.is_empty():
				last_error = message
	if ws != null:
		ws.append_log(record)


## Starts the bridge on demand (used by the dock panel when auto_start is off).
##
## Returns:
##   The fresh get_status() snapshot.
func start_server() -> Dictionary:
	if ws != null and bool(ws.is_running()):
		return get_status()
	_start_transport(true)
	return get_status()


## Stops the bridge and removes the handshake file (manual stop from the panel).
func stop_server() -> void:
	if ws != null:
		ws.stop()
	McpHandshake.clear_handshake()


# ---------------------------------------------------------------------------
# Setup steps
# ---------------------------------------------------------------------------

## Reads (and on first run installs) the plugin ProjectSettings.
func _load_settings() -> void:
	McpHandshake.register_setting(SETTING_AUTO_START, true, {"type": TYPE_BOOL})
	McpHandshake.register_setting(SETTING_PORT, DEFAULT_PORT, {"type": TYPE_INT, "hint": PROPERTY_HINT_RANGE, "hint_string": "1024,65535"})
	auto_start = bool(ProjectSettings.get_setting(SETTING_AUTO_START, true))
	port = int(ProjectSettings.get_setting(SETTING_PORT, DEFAULT_PORT))
	if port < 1024 or port > 65535:
		push_warning("[MCP] port %d is invalid, falling back to %d" % [port, DEFAULT_PORT])
		port = DEFAULT_PORT


## Builds the router and the runtime bridge.
func _create_core(token: String) -> void:
	ws = McpWsServer.new(self)
	ws.set("token", token)
	router = McpRouter.new(self)
	ws.set("router", router)
	runtime = router.call("create_first_module", RUNTIME_OPS_PATHS)
	if runtime == null:
		push_warning("[MCP] runtime ops unavailable, game-process bridge disabled")


## Probes ports, starts the WebSocket server and writes the handshake file.
##
## Args:
##   force: Start even when `auto_start` is disabled (manual start).
func _start_transport(force: bool = false) -> void:
	if ws == null:
		push_error("[MCP] WebSocket server is not initialised")
		return
	if not auto_start and not force:
		print("[MCP] auto_start is disabled, server not started")
		return
	var chosen := McpHandshake.pick_port(port, PORT_SCAN_TRIES)
	if chosen <= 0:
		last_error = "no free port in %d..%d" % [port, port + PORT_SCAN_TRIES - 1]
		push_error("[MCP] " + last_error)
		return
	var bound := int(ws.call("start", chosen))
	if bound <= 0:
		last_error = String(ws.call("last_error"))
		push_error("[MCP] WebSocket server failed to start: " + last_error)
		return
	port = bound
	runtime_port = McpHandshake.pick_port(port + 1, PORT_SCAN_TRIES)
	if runtime_port <= 0:
		runtime_port = port + 1
		push_warning("[MCP] no free runtime port near %d, using %d" % [port + 1, runtime_port])
	_start_runtime()
	McpHandshake.write_handshake(port, runtime_port)


## Starts the in-game bridge on `runtime_port`, honouring its return value.
func _start_runtime() -> void:
	if runtime == null:
		return
	if not runtime.has_method("start"):
		push_warning("[MCP] runtime ops has no start() method, bridge not started")
		return
	var result: Variant = runtime.call("start", runtime_port)
	if typeof(result) == TYPE_INT:
		if int(result) > 0:
			runtime_port = int(result)
		else:
			last_error = "runtime bridge cannot listen on %d" % runtime_port
			push_error("[MCP] " + last_error)
	elif typeof(result) == TYPE_BOOL and not bool(result):
		last_error = "runtime bridge failed to start"
		push_error("[MCP] " + last_error)


## Stops the in-game bridge when this plugin instance owned it.
func _stop_runtime() -> void:
	if runtime == null:
		return
	if runtime.has_method("stop"):
		runtime.call("stop")


## Adds the McpRuntime autoload when the project does not already define one.
func _register_autoload() -> void:
	if ProjectSettings.has_setting(AUTOLOAD_KEY):
		return
	var script_path := ""
	for candidate in AUTOLOAD_SCRIPT_PATHS:
		if ResourceLoader.exists(String(candidate)):
			script_path = String(candidate)
			break
	if script_path.is_empty():
		push_warning("[MCP] autoload script missing: %s" % str(AUTOLOAD_SCRIPT_PATHS[0]))
		return
	ProjectSettings.set_setting(AUTOLOAD_KEY, "*" + script_path)
	_added_autoload = true
	ProjectSettings.save()
	print("[MCP] autoload registered: " + AUTOLOAD_KEY)


## Removes the autoload only when this plugin instance created it.
func _unregister_autoload() -> void:
	if not _added_autoload:
		return
	ProjectSettings.set_setting(AUTOLOAD_KEY, null)
	_added_autoload = false
	ProjectSettings.save()
	print("[MCP] autoload removed: " + AUTOLOAD_KEY)


## Instantiates the dock panel provided by the UI engineer.
func _build_ui() -> void:
	if router == null:
		push_error("[MCP] cannot build UI without a router")
		return
	var panel: Object = router.call("create_first_module", DOCK_PANEL_PATHS)
	if panel == null:
		push_warning("[MCP] dock panel unavailable, continuing without UI")
		return
	if not (panel is Control):
		push_error("[MCP] dock panel script must extend Control")
		return
	_dock = panel as Control
	add_control_to_bottom_panel(_dock, DOCK_SLOT_NAME)


func _remove_ui() -> void:
	if _dock == null:
		return
	if is_instance_valid(_dock):
		remove_control_from_bottom_panel(_dock)
		_dock.queue_free()
	_dock = null


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

func _has_runtime_bridge() -> bool:
	if runtime == null:
		return false
	if runtime.has_method("is_running"):
		return bool(runtime.call("is_running"))
	return runtime_port > 0
