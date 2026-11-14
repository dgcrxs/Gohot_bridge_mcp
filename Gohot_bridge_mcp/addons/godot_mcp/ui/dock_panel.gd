@tool
extends Control

## Godot Bridge MCP dock panel (bottom panel slot "MCP").
##
## Built entirely from code: status header, server/token/client/tool rows and the
## log view. A 0.5 s Timer refreshes the status line and pulls new log entries.
## Everything resolves through the plugin instance (`host`), never through scene
## tree node paths, so the panel survives being re-parented.

const LOG_VIEW_PATH := "res://addons/godot_mcp/ui/log_view.gd"
const SETTING_PORT := "mcp_bridge/port"
const SETTING_AUTO_START := "mcp_bridge/auto_start"
const README_PATH := "res://addons/godot_mcp/README.md"
const SERVER_SCRIPT_PATH := "res://addons/godot_mcp/server.py"
const PYTHON_COMMAND := "python"

const MIN_PORT := 1024
const MAX_PORT := 65535
const REFRESH_INTERVAL := 0.5
const LOG_LIMIT := 200
const NOTICE_LIFETIME_MS := 4000

const CLIENT_NAMES := ["Claude Code", "Cursor", "OpenCode", "Cline", "Codex"]

const COLOR_OK := Color(0.42, 0.78, 0.45)
const COLOR_WARN := Color(0.92, 0.75, 0.28)
const COLOR_IDLE := Color(0.55, 0.56, 0.60)
const COLOR_TEXT := Color(0.80, 0.82, 0.86)

## Owning EditorPlugin (provides get_status(), start_server(), stop_server()).
var host: Variant = null

var _log_view: Control = null
var _dot: ColorRect = null
var _status_label: Label = null
var _port_edit: LineEdit = null
var _auto_check: CheckBox = null
var _token_edit: LineEdit = null
var _client_pick: OptionButton = null
var _client_label: Label = null
var _runtime_label: Label = null
var _timer: Timer = null
var _log_since := 0
var _notice := ""
var _notice_until := 0


func _init(plugin: Variant = null) -> void:
	host = plugin
	name = "GodotBridgeMcpDock"
	custom_minimum_size = Vector2(0, 220)
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_build()


func _ready() -> void:
	_refresh_status()
	_refresh_logs()
# --------------------------------------------------------------------- layout
## Builds the whole panel; every node is created here with add_child().
func _build() -> void:
	var root := VBoxContainer.new()
	root.name = "Layout"
	root.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	root.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	root.size_flags_vertical = Control.SIZE_EXPAND_FILL
	root.add_theme_constant_override("separation", 4)
	add_child(root)
	root.add_child(_build_header())
	root.add_child(_build_server_row())
	root.add_child(_build_token_row())
	root.add_child(_build_client_row())
	root.add_child(_build_tools_row())
	_build_log_view()
	if _log_view != null:
		root.add_child(_log_view)
	_timer = Timer.new()
	_timer.wait_time = REFRESH_INTERVAL
	_timer.autostart = true
	add_child(_timer)
	_timer.timeout.connect(_on_tick)

func _build_header() -> Control:
	var row := _row()
	_dot = ColorRect.new()
	_dot.custom_minimum_size = Vector2(12, 12)
	_dot.color = COLOR_IDLE
	_dot.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.add_child(_dot)
	var title := Label.new()
	title.text = "Godot Bridge MCP"
	title.add_theme_font_size_override("font_size", 15)
	row.add_child(title)
	_status_label = _make_label("Stopped")
	_status_label.clip_text = true
	_status_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	_status_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_status_label.add_theme_color_override("font_color", COLOR_IDLE)
	row.add_child(_status_label)
	return row

func _build_server_row() -> Control:
	var row := _row()
	row.add_child(_make_label("Port"))
	_port_edit = LineEdit.new()
	_port_edit.custom_minimum_size = Vector2(90, 0)
	_port_edit.max_length = 5
	_port_edit.placeholder_text = "8765"
	_port_edit.text = "8765"
	row.add_child(_port_edit)
	_auto_check = CheckBox.new()
	_auto_check.text = "Auto start"
	_auto_check.button_pressed = true
	_auto_check.toggled.connect(_on_auto_toggled)
	row.add_child(_auto_check)
	row.add_child(_make_button("Start", _on_start_pressed))
	row.add_child(_make_button("Stop", _on_stop_pressed))
	row.add_child(_make_button("Apply port", _on_apply_port))
	row.add_child(_spacer())
	return row

func _build_token_row() -> Control:
	var row := _row()
	row.add_child(_make_label("Token"))
	_token_edit = LineEdit.new()
	_token_edit.secret = true
	_token_edit.editable = false
	_token_edit.custom_minimum_size = Vector2(180, 0)
	_token_edit.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_token_edit.tooltip_text = "Shared auth token read from the project settings."
	row.add_child(_token_edit)
	row.add_child(_make_button("Copy", _on_copy_token))
	_client_label = _make_label("clients: 0")
	row.add_child(_client_label)
	_runtime_label = _make_label("game: offline")
	row.add_child(_runtime_label)
	return row

func _build_client_row() -> Control:
	var row := _row()
	row.add_child(_make_label("Client"))
	_client_pick = OptionButton.new()
	for client_name in CLIENT_NAMES:
		_client_pick.add_item(String(client_name))
	_client_pick.select(0)
	_client_pick.custom_minimum_size = Vector2(140, 0)
	row.add_child(_client_pick)
	row.add_child(_make_button("Copy config", _on_copy_config))
	row.add_child(_spacer())
	return row

func _build_tools_row() -> Control:
	var row := _row()
	row.add_child(_make_button("Clear logs", _on_clear_logs))
	row.add_child(_make_button("Open README", _on_open_readme))
	row.add_child(_spacer())
	return row

## Instantiates ui/log_view.gd, falling back to a bare RichTextLabel.
func _build_log_view() -> void:
	var resource: Variant = load(LOG_VIEW_PATH) if ResourceLoader.exists(LOG_VIEW_PATH) else null
	var instance: Variant = (resource as Script).new() if resource is Script else null
	if instance is Control:
		_log_view = instance as Control
	if _log_view == null:
		_log_view = RichTextLabel.new()
		_log_view.custom_minimum_size = Vector2(0, 160)
	_log_view.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_log_view.size_flags_vertical = Control.SIZE_EXPAND_FILL

func _row() -> HBoxContainer:
	var row := HBoxContainer.new()
	row.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_theme_constant_override("separation", 6)
	return row

func _spacer() -> Control:
	var spacer := Control.new()
	spacer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	return spacer

func _make_label(text: String) -> Label:
	var label := Label.new()
	label.text = text
	label.add_theme_color_override("font_color", COLOR_TEXT)
	return label

func _make_button(text: String, handler: Callable) -> Button:
	var button := Button.new()
	button.text = text
	button.pressed.connect(handler)
	return button
# -------------------------------------------------------------------- refresh
func _on_tick() -> void:
	if not is_inside_tree(): return
	_refresh_status()
	_refresh_logs()

## Updates the dot, the port/token fields and the status text.
func _refresh_status() -> void:
	var status := _status()
	var running := bool(status.get("running", false))
	var clients := int(status.get("clients", 0))
	var bound := int(status.get("port", 0))
	var connected := _runtime_connected()
	var dot_colour := COLOR_IDLE
	if running:
		dot_colour = COLOR_OK if clients > 0 or connected else COLOR_WARN
	if _alive(_dot):
		_dot.color = dot_colour
	if _alive(_auto_check):
		_auto_check.set_pressed_no_signal(bool(status.get("auto_start", true)))
	if _alive(_port_edit) and not _port_edit.has_focus() and bound > 0:
		_port_edit.text = str(bound)
	if _alive(_token_edit) and _token_edit.text != String(status.get("token", "")):
		_token_edit.text = String(status.get("token", ""))
	if _alive(_client_label):
		_client_label.text = "clients: %d" % clients
	if _alive(_runtime_label):
		_runtime_label.text = "game: %s" % ("connected" if connected else "offline")
	var parts: Array = ["Listening on 127.0.0.1:%d" % bound if running else "Stopped"]
	if connected: parts.append("runtime bridge up")
	var last_error := String(status.get("last_error", ""))
	if not last_error.is_empty(): parts.append("error: " + last_error)
	if Time.get_ticks_msec() < _notice_until and not _notice.is_empty(): parts.append(_notice)
	_set_status(" | ".join(parts), dot_colour)

## Null and instance validity in one check (nodes may be freed by the editor).
func _alive(node: Variant) -> bool:
	return node != null and is_instance_valid(node)

## Pulls new transport log entries into the log view.
func _refresh_logs() -> void:
	if not _alive(_log_view) or not _log_view.has_method("append"):
		return
	var server := _server()
	if server == null:
		return
	var raw: Variant = server.call("get_logs", _log_since, "", LOG_LIMIT)
	if typeof(raw) != TYPE_DICTIONARY:
		return
	var data: Dictionary = raw
	var entries: Variant = data.get("entries", [])
	if typeof(entries) == TYPE_ARRAY:
		for item in entries:
			if typeof(item) == TYPE_DICTIONARY:
				_log_view.call("append", item)
	_log_since = int(data.get("next", _log_since))

func _set_status(text: String, colour: Color) -> void:
	if not _alive(_status_label):
		return
	_status_label.text = text
	_status_label.add_theme_color_override("font_color", colour)

func _set_notice(text: String) -> void:
	_notice = text
	_notice_until = Time.get_ticks_msec() + NOTICE_LIFETIME_MS
# ------------------------------------------------------------------- handlers
func _on_start_pressed() -> void:
	var plugin := _plugin()
	if plugin == null or not plugin.has_method("start_server"):
		_set_notice("Bridge API unavailable")
		return
	plugin.call("start_server")
	_refresh_status()

func _on_stop_pressed() -> void:
	var plugin := _plugin()
	if plugin != null and plugin.has_method("stop_server"):
		plugin.call("stop_server")
	_refresh_status()

## Validates the port, persists it, then restarts the WebSocket server.
func _on_apply_port() -> void:
	if not _alive(_port_edit):
		return
	var text := _port_edit.text.strip_edges()
	var plugin := _plugin()
	if plugin == null:
		_apply_failed("bridge API unavailable")
		return
	if not text.is_valid_int():
		_apply_failed("port '%s' is not a number" % text)
		return
	var new_port := text.to_int()
	if new_port < MIN_PORT or new_port > MAX_PORT:
		_apply_failed("port %d is out of range %d-%d" % [new_port, MIN_PORT, MAX_PORT])
		return
	plugin.set("port", new_port)
	ProjectSettings.set_setting(SETTING_PORT, new_port)
	ProjectSettings.save()
	if plugin.has_method("stop_server"): plugin.call("stop_server")
	if plugin.has_method("start_server"): plugin.call("start_server")
	_refresh_status()
	var status := _status()
	if not bool(status.get("running", false)):
		var reason := String(status.get("last_error", ""))
		_apply_failed("server did not start" if reason.is_empty() else reason)
		return
	_set_notice("port applied: %d" % int(status.get("port", new_port)))

func _apply_failed(reason: String) -> void:
	push_error("[MCP] cannot apply port: " + reason)
	_set_status("Apply port failed: " + reason, COLOR_WARN)

func _on_auto_toggled(pressed: bool) -> void:
	var plugin := _plugin()
	if plugin != null:
		plugin.set("auto_start", pressed)
	ProjectSettings.set_setting(SETTING_AUTO_START, pressed)
	ProjectSettings.save()
	_refresh_status()

func _on_copy_token() -> void:
	if not _alive(_token_edit):
		return
	if _token_edit.text.is_empty():
		_set_notice("token is empty (auth disabled)")
		return
	DisplayServer.clipboard_set(_token_edit.text)
	_set_notice("token copied")

## Copies a minimal MCP client configuration for the selected client.
func _on_copy_config() -> void:
	var client := _selected_client()
	DisplayServer.clipboard_set(_config_text(client))
	_set_notice("%s config copied" % client)

func _on_clear_logs() -> void:
	if _alive(_log_view) and _log_view.has_method("clear"):
		_log_view.call("clear")
	var server := _server()
	if server != null and server.has_method("clear_logs"): server.call("clear_logs")
	_set_notice("logs cleared")

func _on_open_readme() -> void:
	if not ResourceLoader.exists(README_PATH):
		push_error("[MCP] README not found: " + README_PATH)
		_set_notice("README missing")
		return
	if OS.shell_open(ProjectSettings.globalize_path(README_PATH)) != OK:
		push_error("[MCP] cannot open " + README_PATH)
		_set_notice("cannot open README")
		return
	_set_notice("README opened")
# --------------------------------------------------------- client config text
## Absolute path of the bundled python server, or the res:// path as fallback.
func _server_path() -> String:
	var absolute := ProjectSettings.globalize_path(SERVER_SCRIPT_PATH)
	return absolute if FileAccess.file_exists(absolute) else SERVER_SCRIPT_PATH

## JSON snippet for one MCP client.
func _config_text(client: String) -> String:
	var path := _server_path()
	var entry := {"command": PYTHON_COMMAND, "args": [path]}
	var data: Dictionary = {}
	match client:
		"OpenCode":
			data = {"$schema": "https://opencode.ai/config.json",
				"mcp": {"godot-bridge": {"type": "local", "command": [PYTHON_COMMAND, path], "enabled": true}}}
		"Cursor":
			data = {"mcpServers": {"godot-bridge": entry}}
		"Cline":
			data = {"mcpServers": {"godot-bridge": {"command": PYTHON_COMMAND, "args": [path],
				"disabled": false, "autoApprove": []}}}
		"Codex":
			data = {"mcp_servers": {"godot-bridge": entry}}
		_:
			data = {"mcpServers": {"godot-bridge": {"command": PYTHON_COMMAND, "args": [path], "env": {}}}}
	return JSON.stringify(data, "  ")
# ----------------------------------------------------- host accessors (null safe)
## Selected client name in the OptionButton.
func _selected_client() -> String:
	if _client_pick == null or not is_instance_valid(_client_pick):
		return String(CLIENT_NAMES[0])
	var index := _client_pick.selected
	if index < 0 or index >= CLIENT_NAMES.size():
		return String(CLIENT_NAMES[0])
	return String(CLIENT_NAMES[index])

func _plugin() -> Object:
	var plugin: Object = host
	return null if plugin == null or not (plugin is Object) else plugin

func _status() -> Dictionary:
	var plugin := _plugin()
	if plugin == null or not plugin.has_method("get_status"):
		return {}
	var raw: Variant = plugin.call("get_status")
	return raw if typeof(raw) == TYPE_DICTIONARY else {}

## Transport server (ws_server.gd) - may be null while the plugin boots.
func _server() -> Object:
	var plugin := _plugin()
	if plugin == null: return null
	var raw: Variant = plugin.get("ws")
	if typeof(raw) != TYPE_OBJECT or raw == null:
		return null
	var server: Object = raw
	return server if server.has_method("get_logs") else null

## True when the game process bridge reports an authenticated game.
func _runtime_connected() -> bool:
	var plugin := _plugin()
	if plugin == null: return false
	var raw: Variant = plugin.get("runtime")
	if typeof(raw) != TYPE_OBJECT or raw == null:
		return false
	var bridge: Object = raw
	if not bridge.has_method("snapshot"):
		return false
	var snapshot: Variant = bridge.call("snapshot")
	if typeof(snapshot) != TYPE_DICTIONARY:
		return false
	return bool((snapshot as Dictionary).get("connected", false))
