@tool
extends Node

## McpRuntime - autoload that runs inside the *game* process and answers the
## editor bridge (PROTOCOL.md §3.9).
##
## It reads `.godot/mcp_bridge.json` for `runtime_port`/`token`, connects to
## ws://127.0.0.1:<port>, authenticates, and retries with backoff (1/2/4 s) up to
## MAX_ATTEMPTS. The editor sends {"id":"r1","method":"logs","params":{}} and this
## node answers {"id":"r1","result":{...}} or {"id":"r1","error":{...}}.
##
## The script is @tool so the autoload also loads in the editor without errors;
## _ready() bails out immediately in the editor process.

const McpValue = preload("res://addons/godot_mcp/core/value_codec.gd")

const HANDSHAKE_PATHS := ["res://.godot/mcp_bridge.json", "user://mcp_bridge.json"]
const MAX_LOG_ENTRIES := 500
const MAX_ATTEMPTS := 5
const CONNECT_RETRY_DELAY := 3.0
const RETRY_DELAYS := [1.0, 2.0, 4.0]
const DEFAULT_MAX_SIZE := 1280
const MAX_PROPERTIES := 100
const EVAL_HEADER := "extends Node\n\nfunc _mcp_run():\n"
const EVAL_FALLBACK := "\treturn null\n"
const TAB := "\t"
const CLOSE_SHUTDOWN := 1001
const HEADLESS_HINT := "The game is running headless or rendering is disabled."

var _peer: WebSocketPeer = null
var _url := ""
var _token := ""
var _port := 0
var _connected := false
var _authed := false
var _attempts := 0
var _retry_pending := false
var _shutting_down := false
var _logs: Array = []
var _log_seq := 0


func _ready() -> void:
	# The autoload also exists in the editor process; never connect from there.
	if Engine.is_editor_hint():
		set_process(false)
		return
	if not _load_handshake():
		set_process(false)
		return
	_connect()
	set_process(true)


func _process(_delta: float) -> void:
	if _peer == null:
		if not _retry_pending and not _shutting_down:
			_connect()
		return
	_peer.poll()
	match _peer.get_ready_state():
		WebSocketPeer.STATE_OPEN:
			if not _connected:
				_connected = true
				_authed = false
				_attempts = 0
				_send({"method": "auth", "params": {"token": _token, "pid": OS.get_process_id()}})
			_drain_packets()
		WebSocketPeer.STATE_CLOSED:
			_on_disconnected()


func _exit_tree() -> void:
	_shutting_down = true
	if _peer != null:
		_peer.close(CLOSE_SHUTDOWN, "game shutting down")
		_peer = null

# ------------------------------------------------------------------ connection
## Reads the handshake file written by the editor plugin.
## Returns true when a usable runtime port and token were found.
func _load_handshake() -> bool:
	for path in HANDSHAKE_PATHS:
		if not FileAccess.file_exists(String(path)):
			continue
		var file := FileAccess.open(String(path), FileAccess.READ)
		if file == null:
			continue
		var text := file.get_as_text()
		file.close()
		var parser := JSON.new()
		if parser.parse(text) != OK:
			continue
		var data: Variant = parser.get_data()
		if typeof(data) != TYPE_DICTIONARY:
			continue
		var record: Dictionary = data
		_port = int(record.get("runtime_port", 0))
		if _port <= 0:
			continue
		_token = String(record.get("token", ""))
		_url = "ws://127.0.0.1:%d" % _port
		return true
	push_warning("[MCP] runtime bridge: no handshake file, the bridge stays idle")
	return false

## Opens one WebSocketPeer toward the editor bridge.
func _connect() -> void:
	if _port <= 0 or not is_inside_tree():
		return
	var peer := WebSocketPeer.new()
	if peer.connect_to_url(_url) != OK:
		_peer = null
		_log("warn", "cannot reach the editor bridge at %s" % _url)
		_schedule_retry(CONNECT_RETRY_DELAY)
		return
	_peer = peer
	_connected = false
	_authed = false

## Schedules one reconnect attempt with backoff.
func _schedule_retry(delay: float) -> void:
	if _retry_pending or _shutting_down or not is_inside_tree():
		return
	if _attempts >= MAX_ATTEMPTS:
		_log("warn", "runtime bridge disabled after %d failed attempts" % MAX_ATTEMPTS)
		set_process(false)
		return
	_retry_pending = true
	_attempts += 1
	await get_tree().create_timer(delay).timeout
	_retry_pending = false
	if _shutting_down or not is_inside_tree():
		return
	_connect()

## Handles a closed socket: drops the peer and asks for a reconnect.
func _on_disconnected() -> void:
	var was_connected := _connected
	_connected = false
	_authed = false
	_peer = null
	if _shutting_down:
		return
	var delay := float(RETRY_DELAYS[mini(_attempts, RETRY_DELAYS.size() - 1)])
	if was_connected:
		_attempts = 0
		delay = 1.0
		_log("warn", "editor bridge disconnected, reconnecting")
	_schedule_retry(delay)

func _drain_packets() -> void:
	var guard := 0
	while _peer != null and _peer.get_available_packet_count() > 0 and guard < 16:
		guard += 1
		var packet := _peer.get_packet()
		if _peer.was_string_packet() and not packet.is_empty():
			_handle_text(packet.get_string_from_utf8())

# -------------------------------------------------------------- request router
## Parses one editor frame and answers it.
func _handle_text(text: String) -> void:
	if text.is_empty(): return
	var parser := JSON.new()
	if parser.parse(text) != OK:
		_log("error", "runtime bridge received malformed JSON")
		return
	var data: Variant = parser.get_data()
	if typeof(data) != TYPE_DICTIONARY: return
	var message: Dictionary = data
	var request_id := str(message.get("id", ""))
	var method := String(message.get("method", ""))
	if request_id.is_empty() or method.is_empty(): return
	var params: Dictionary = {}
	var raw_params: Variant = message.get("params", {})
	if typeof(raw_params) == TYPE_DICTIONARY:
		params = raw_params
	match method:
		"ping":
			_send_result(request_id, {"pong": true, "running": true})
		"logs":
			_send_result(request_id, _logs_payload(params))
		"get_tree":
			_handle_get_tree(request_id, params)
		"get_node":
			_handle_get_node(request_id, params)
		"eval":
			_handle_eval(request_id, params)
		"screenshot":
			_handle_screenshot(request_id, params)
		"input":
			# Ack first: the editor only waits TIMEOUT_ACK_MS for this frame.
			_send_result(request_id, {"sent": true})
			_handle_input(params)
		"auth":
			_send_result(request_id, {"ok": true, "client_id": "game"})
		_:
			_send_error(request_id, -32601,
				"method '%s' is not supported by the game bridge" % method,
				"Supported: ping, logs, get_tree, get_node, eval, screenshot, input.")

## PROTOCOL.md §3.9 runtime.logs - this node's own buffer.
func _logs_payload(params: Dictionary) -> Dictionary:
	var since := int(params.get("since", 0))
	var level := String(params.get("level", ""))
	var limit := maxi(int(params.get("limit", 200)), 1)
	var entries: Array = []
	for item in _logs:
		var record: Dictionary = item
		if int(record.get("seq", 0)) <= since:
			continue
		if level.is_empty() or String(record.get("level", "")) == level:
			entries.append(record.duplicate(true))
	if entries.size() > limit:
		entries = entries.slice(entries.size() - limit)
	return {"entries": entries, "next": _log_seq, "running": true}

## PROTOCOL.md §3.9 runtime.get_tree.
func _handle_get_tree(request_id: String, params: Dictionary) -> void:
	var tree := get_tree()
	if tree == null or tree.root == null:
		_send_error(request_id, 1001, "the game has no scene tree yet", "Call godot_run_scene first.")
		return
	var root_node := _tree_node(tree.root, int(params.get("depth", -1)), 0)
	_send_result(request_id, {"root": root_node, "count": _count_nodes(root_node)})

## Recursively describes a node and its children. `depth` < 0 means unlimited.
func _tree_node(node: Node, depth: int, level: int) -> Dictionary:
	var children: Array = []
	if depth < 0 or level < depth:
		for child in node.get_children():
			if String(child.name).begins_with("@"):
				continue
			children.append(_tree_node(child, depth, level + 1))
	return {"name": String(node.name), "type": node.get_class(),
		"path": _path_of(node), "children": children}

func _count_nodes(node: Dictionary) -> int:
	var total := 1
	var children: Variant = node.get("children", [])
	if typeof(children) != TYPE_ARRAY:
		return total
	for child in children:
		if typeof(child) == TYPE_DICTIONARY:
			total += _count_nodes(child)
	return total

## PROTOCOL.md §3.9 runtime.get_node.
func _handle_get_node(request_id: String, params: Dictionary) -> void:
	var tree := get_tree()
	if tree == null or tree.root == null:
		_send_error(request_id, 1001, "the game has no scene tree yet", "Call godot_run_scene first.")
		return
	var path := String(params.get("path", ""))
	var target: Node = null
	if path.is_empty() or path == "." or path == "/root" or path == "/root/":
		target = tree.root
	elif path.begins_with("/root/"):
		target = tree.root.get_node_or_null(NodePath(path.trim_prefix("/root/")))
	else:
		target = tree.root.get_node_or_null(NodePath(path))
	if target == null:
		_send_error(request_id, 1002, "node not found: '%s'" % path,
			"Call godot_get_runtime_tree to list valid node paths.", {"path": path})
		return
	var info := {"name": String(target.name), "type": target.get_class(), "path": _path_of(target)}
	var attached := target.get_script()
	if attached is Script:
		var script_path := String((attached as Script).resource_path)
		if not script_path.is_empty():
			info["script"] = script_path
	if bool(params.get("with_props", true)):
		info["properties"] = _properties_of(target)
	_send_result(request_id, info)

## Reads at most MAX_PROPERTIES stored properties, encoded with the value codec.
func _properties_of(node: Node) -> Dictionary:
	var properties: Dictionary = {}
	var count := 0
	for item in node.get_property_list():
		if count >= MAX_PROPERTIES:
			break
		var data: Dictionary = item
		var usage := int(data.get("usage", 0))
		if (usage & PROPERTY_USAGE_STORAGE) == 0 and (usage & PROPERTY_USAGE_SCRIPT_VARIABLE) == 0:
			continue
		var property_name := String(data.get("name", ""))
		if property_name.is_empty():
			continue
		properties[property_name] = McpValue.encode(node.get(property_name))
		count += 1
	return properties

## PROTOCOL.md §3.12 runtime.eval - runs the snippet in the game process.
func _handle_eval(request_id: String, params: Dictionary) -> void:
	var code := String(params.get("code", ""))
	if code.strip_edges().is_empty():
		_send_error(request_id, -32602, "missing required param 'code'",
			"Send the statements to run, e.g. {\"code\":\"return 1 + 1\"}.", {"param": "code"})
		return
	if not is_inside_tree(): return
	var script := GDScript.new()
	script.source_code = _wrap_eval(code)
	var error := script.reload()
	if error != OK:
		_log("error", "eval compile error: %s" % error_string(error))
		_send_error(request_id, 1007, "script compile error: %s" % error_string(error),
			"Check the syntax; the wrapper expects statements, not a full script.", {"code": code})
		return
	var host_node := Node.new()
	host_node.set_script(script)
	get_tree().root.add_child(host_node)
	var result: Variant = null
	if host_node.has_method("_mcp_run"):
		result = host_node.call("_mcp_run")
	var payload := McpValue.encode(result)
	# The snippet may have reparented the wrapper itself, so re-check the parent.
	if host_node.get_parent() != null:
		host_node.get_parent().remove_child(host_node)
	host_node.queue_free()
	_log("info", "eval ok")
	_send_result(request_id, {"result": payload})

## Wraps a statement block into a Node script with one tab of indentation.
func _wrap_eval(code: String) -> String:
	var indented := ""
	for line in code.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
		indented += "\n" if line.strip_edges().is_empty() else TAB + line + "\n"
	if indented.strip_edges().is_empty():
		indented = EVAL_FALLBACK
	return EVAL_HEADER + indented

## PROTOCOL.md §3.9 runtime.screenshot.
func _handle_screenshot(request_id: String, params: Dictionary) -> void:
	var max_size := maxi(int(params.get("max_size", DEFAULT_MAX_SIZE)), 1)
	if not _can_render():
		_log("warn", "screenshot refused: no render target")
		_send_error(request_id, 1008, "The game is rendering no frames", HEADLESS_HINT)
		return
	await RenderingServer.frame_post_draw
	var viewport := get_viewport() if is_inside_tree() else null
	var texture := viewport.get_texture() if viewport != null else null
	var image := texture.get_image() if texture != null else null
	if image == null or image.get_width() <= 0 or image.get_height() <= 0:
		_log("warn", "screenshot refused: empty frame")
		_send_error(request_id, 1008, "The captured frame is empty", HEADLESS_HINT)
		return
	var longest := maxi(image.get_width(), image.get_height())
	if longest > max_size:
		var scale := float(max_size) / float(longest)
		image.resize(maxi(1, int(round(float(image.get_width()) * scale))),
			maxi(1, int(round(float(image.get_height()) * scale))), Image.INTERPOLATE_BILINEAR)
	var buffer := image.save_png_to_buffer()
	if buffer.is_empty():
		_send_error(request_id, 1008, "The PNG buffer is empty", HEADLESS_HINT)
		return
	_log("info", "screenshot %dx%d" % [image.get_width(), image.get_height()])
	_send_result(request_id, {"png_base64": Marshalls.raw_to_base64(buffer),
		"width": image.get_width(), "height": image.get_height()})

## True when the game process can actually produce frames.
func _can_render() -> bool:
	if DisplayServer.get_name() == "headless" or not is_inside_tree():
		return false
	var viewport := get_viewport()
	if viewport == null:
		return false
	var size := viewport.get_visible_rect().size
	return size.x > 0.0 and size.y > 0.0

## PROTOCOL.md §3.9 runtime.input - plays every event in order, waiting between.
func _handle_input(params: Dictionary) -> void:
	var events: Variant = params.get("events", [])
	if typeof(events) != TYPE_ARRAY: return
	for item in events:
		if typeof(item) != TYPE_DICTIONARY:
			continue
		await _play_event(item)
		await get_tree().process_frame

## Executes one input event from the protocol list.
func _play_event(event: Dictionary) -> void:
	match String(event.get("type", "")):
		"key":
			_play_key(event)
		"mouse":
			_play_mouse_button(event)
		"motion":
			_play_mouse_motion(event)
		"action":
			_play_action(event)
		"wait":
			await get_tree().create_timer(maxf(0.0, float(event.get("ms", 0))) / 1000.0).timeout

func _play_key(event: Dictionary) -> void:
	var key_name := String(event.get("keycode", "")).strip_edges().to_upper()
	if key_name.is_empty(): return
	var code := OS.find_keycode_from_string(key_name)
	if code == KEY_NONE:
		_log("warn", "unknown keycode '%s'" % key_name)
		return
	var input_event := InputEventKey.new()
	input_event.keycode = code
	input_event.physical_keycode = code
	input_event.pressed = bool(event.get("pressed", true))
	input_event.shift_pressed = bool(event.get("shift", false))
	input_event.ctrl_pressed = bool(event.get("ctrl", false))
	input_event.alt_pressed = bool(event.get("alt", false))
	input_event.meta_pressed = bool(event.get("meta", false))
	Input.parse_input_event(input_event)

func _play_mouse_button(event: Dictionary) -> void:
	var names := {"left": MOUSE_BUTTON_LEFT, "right": MOUSE_BUTTON_RIGHT, "middle": MOUSE_BUTTON_MIDDLE}
	var position := Vector2(float(event.get("x", 0.0)), float(event.get("y", 0.0)))
	var input_event := InputEventMouseButton.new()
	input_event.button_index = int(names.get(String(event.get("button", "left")).to_lower(),
		MOUSE_BUTTON_LEFT))
	input_event.pressed = bool(event.get("pressed", true))
	input_event.double_click = bool(event.get("double", false))
	input_event.position = position
	input_event.global_position = position
	Input.parse_input_event(input_event)

func _play_mouse_motion(event: Dictionary) -> void:
	var position := Vector2(float(event.get("x", 0.0)), float(event.get("y", 0.0)))
	var input_event := InputEventMouseMotion.new()
	input_event.position = position
	input_event.global_position = position
	input_event.relative = Vector2(float(event.get("dx", 0.0)), float(event.get("dy", 0.0)))
	Input.parse_input_event(input_event)

func _play_action(event: Dictionary) -> void:
	var action := String(event.get("action", ""))
	if action.is_empty(): return
	if bool(event.get("pressed", true)):
		Input.action_press(action, float(event.get("strength", 1.0)))
	else:
		Input.action_release(action)

# ---------------------------------------------------------------- sending / log
func _send(payload: Dictionary) -> void:
	if _peer == null or _peer.get_ready_state() != WebSocketPeer.STATE_OPEN:
		return
	_peer.send_text(JSON.stringify(payload))

func _send_result(request_id: String, result: Dictionary) -> void:
	_send({"id": request_id, "result": result})

func _send_error(request_id: String, code: int, message: String,
		hint: String = "", context: Dictionary = {}) -> void:
	_send({"id": request_id, "error": {"code": code, "message": message,
		"hint": hint, "context": context}})

## Appends one record to the local buffer (PROTOCOL.md §3.9 runtime.logs).
func _log(level: String, text: String) -> void:
	if text.strip_edges().is_empty(): return
	_log_seq += 1
	_logs.append({"seq": _log_seq, "ts": int(Time.get_unix_time_from_system()),
		"level": level, "text": text})
	while _logs.size() > MAX_LOG_ENTRIES:
		_logs.pop_front()

## Path of a node relative to the scene tree root (PROTOCOL.md §3.9).
func _path_of(node: Node) -> String:
	if not is_inside_tree() or get_tree() == null:
		return String(node.name)
	var root := get_tree().root
	if root == null or node == root:
		return "."
	return String(root.get_path_to(node))
