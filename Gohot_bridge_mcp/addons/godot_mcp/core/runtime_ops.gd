@tool
extends RefCounted

## Runtime layer (PROTOCOL.md §3.9): editor side of the game-process bridge.
##
## A TCPServer accepts WebSocket connections from the `McpRuntime` autoload that
## runs inside the launched game; frames are {"id":"r1","method":"logs"} ->
## {"id":"r1","result":{...}} / {"id":"r1","error":{...}}. handle() is
## synchronous, so a game call pumps the socket until its id arrives.
##
## Public API: start(port) -> int, stop(), poll(), is_running(), snapshot().

const McpErrors = preload("res://addons/godot_mcp/core/errors.gd")

const BIND_ADDRESS := "127.0.0.1"
const MAX_PEERS := 4
const MAX_PACKETS_PER_POLL := 32
const POLL_DELAY_MS := 5
const CLOSE_AUTH_FAILED := 4001
const CLOSE_SHUTDOWN := 1001
const TIMEOUT_DATA_MS := 3000
const TIMEOUT_SCREENSHOT_MS := 5000
const TIMEOUT_EVAL_MS := 8000
const TIMEOUT_RUN_MS := 20000
const TIMEOUT_ACK_MS := 1000
const MAX_MERGED := 1000
const CAPTURE_PATH := "res://addons/godot_mcp/core/debugger_capture.gd"
const HINT_RUN := "Call godot_run_scene first, then retry."
const HINT_TREE := "Call godot_get_runtime_tree to list node paths."

## Owning EditorPlugin. Provides get_token() / get_editor_interface().
var host: Variant = null
var _server: TCPServer = null
var _port := 0
var _peers: Dictionary = {}
var _next_peer := 1
var _game: Dictionary = {}
var _pending: Dictionary = {}
var _next_request := 0
var _capture: Object = null
var _last_error := ""
var _merged: Array = []
var _merged_seq := 0
var _game_cursor := 0
var _capture_cursor := 0

func _init(plugin: Variant = null) -> void:
	host = plugin
	_ensure_capture()

## Starts the game side listener. Returns the bound port, or -1 on failure.
func start(bind_port: int) -> int:
	stop()
	if bind_port <= 0 or bind_port > 65535:
		_last_error = "invalid runtime port %d" % bind_port
		push_warning("[MCP] " + _last_error)
		return -1
	var server := TCPServer.new()
	var error := server.listen(bind_port, BIND_ADDRESS)
	if error != OK:
		_last_error = "cannot listen on %s:%d (err %d)" % [BIND_ADDRESS, bind_port, error]
		push_warning("[MCP] runtime bridge " + _last_error)
		return -1
	_server = server
	_port = bind_port
	print("[MCP] runtime bridge listening on %s:%d" % [BIND_ADDRESS, bind_port])
	return _port
## Stops the listener and drops every game connection.
func stop() -> void:
	for peer_id in _peers.keys():
		_close_peer(String(peer_id), "runtime bridge stopped", CLOSE_SHUTDOWN)
	_peers.clear()
	_game = {}
	_pending.clear()
	_game_cursor = 0
	if _server != null:
		_server.stop()
		_server = null
	if _port > 0:
		print("[MCP] runtime bridge stopped")
	_port = 0
## Advances the bridge state machine; driven by plugin._process().
func poll() -> void:
	if _server != null:
		_pump()
## True while the listener socket is open.
func is_running() -> bool:
	return _server != null
## Non-blocking snapshot for the dock panel; never talks to the game.
func snapshot() -> Dictionary:
	return {"listening": _server != null, "port": _port, "connected": not _game.is_empty(),
		"peers": _peers.size(), "pid": int(_game.get("pid", 0)),
		"has_capture": _capture != null, "last_error": _last_error}

## Routes one `runtime.*` method; never raises, always returns a Dictionary.
func handle(action: String, params: Dictionary) -> Dictionary:
	_ensure_capture()
	match action:
		"runtime.status": return _status()
		"runtime.run": return _run(params)
		"runtime.stop": return _stop_game()
		"runtime.logs": return _logs(params)
		"runtime.screenshot": return _screenshot(params)
		"runtime.input": return _input_events(params)
		"runtime.eval": return _eval(params)
		"runtime.get_tree": return _get_tree(params)
		"runtime.get_node": return _get_node(params)
	return _fail(-32601, "method '%s' is not handled by runtime_ops" % action,
		"Call godot_get_capabilities for the method list.", {"method": action})
func _status() -> Dictionary:
	return {"running": _is_playing(), "connected": not _game.is_empty(),
		"scene": _playing_scene(), "pid": int(_game.get("pid", 0))}
## Launches the game (main scene or explicit) then waits for the bridge.
func _run(params: Dictionary) -> Dictionary:
	var editor := _editor()
	if editor == null:
		return _fail(1014, "the editor interface is not available", "Run the bridge from the editor.")
	var scene := String(params.get("scene", "")).strip_edges()
	if not scene.is_empty():
		if not scene.begins_with("res://"):
			return _fail(1005, "'%s' is not a res:// path" % scene,
				"Pass a path such as res://scenes/Main.tscn.", {"scene": scene})
		if not scene.ends_with(".tscn"):
			return _fail(1004, "'%s' is not a .tscn scene" % scene,
				"Pass a .tscn file, not a .scn, .res or script.", {"scene": scene})
		if not ResourceLoader.exists(scene):
			return _fail(1006, "scene not found: '%s'" % scene,
				"Call godot_list_directory to list the scene files.", {"scene": scene})
	if _is_playing() and editor.has_method("stop_playing_scene"):
		editor.call("stop_playing_scene")
	_drop_game("game restarted")
	if scene.is_empty():
		if not editor.has_method("play_main_scene"):
			return _fail(1014, "play_main_scene() is unavailable here", "Pass an explicit 'scene'.")
		editor.call("play_main_scene")
	else:
		if not editor.has_method("play_custom_scene"):
			return _fail(1014, "play_custom_scene() is unavailable here", "Open the scene first.")
		editor.call("play_custom_scene", scene)
	var connected := _wait_for_game(TIMEOUT_RUN_MS)
	var current := _playing_scene()
	if current.is_empty():
		current = scene
	if not connected:
		return {"running": _is_playing(), "scene": current, "connected": false,
			"note": "the game did not connect within %d ms" % TIMEOUT_RUN_MS}
	return {"running": _is_playing(), "scene": current, "connected": true}
## Stops the running game and forgets its connection.
func _stop_game() -> Dictionary:
	var editor := _editor()
	if editor != null and editor.has_method("stop_playing_scene"):
		editor.call("stop_playing_scene")
	_drop_game("game stopped")
	return {"running": false}
func _logs(params: Dictionary) -> Dictionary:
	var since := int(params.get("since", 0))
	var level := String(params.get("level", ""))
	var limit := maxi(int(params.get("limit", 200)), 1)
	_harvest_logs()
	var entries: Array = []
	for item in _merged:
		var record: Dictionary = item
		if int(record.get("seq", 0)) <= since:
			continue
		if level.is_empty() or String(record.get("level", "")) == level:
			entries.append(record.duplicate(true))
	if entries.size() > limit:
		entries = entries.slice(entries.size() - limit)
	return {"entries": entries, "next": _merged_seq, "running": _is_playing()}
func _screenshot(params: Dictionary) -> Dictionary:
	if _game.is_empty(): return _fail(1008, "The game is not running.", HINT_RUN)
	var max_size := maxi(int(params.get("max_size", 1280)), 1)
	var response := _request("screenshot", {"max_size": max_size}, TIMEOUT_SCREENSHOT_MS)
	if McpErrors.is_error(response):
		return response
	var payload := _payload(response)
	var png := String(payload.get("png_base64", ""))
	if png.is_empty():
		return _fail(1008, "The game returned an empty screenshot",
			"The game is running headless or rendering is disabled.")
	return {"png_base64": png, "width": int(payload.get("width", 0)),
		"height": int(payload.get("height", 0))}
func _input_events(params: Dictionary) -> Dictionary:
	var events: Variant = params.get("events", [])
	if typeof(events) != TYPE_ARRAY:
		return _fail(-32602, "'events' must be an array",
			"Example: {\"events\":[{\"type\":\"key\",\"keycode\":\"SPACE\",\"pressed\":true}]}")
	if (events as Array).is_empty():
		return _fail(-32602, "'events' is empty", "Send at least one input event.")
	var response := _request("input", {"events": events}, TIMEOUT_ACK_MS)
	if McpErrors.is_error(response):
		return {"sent": true, "note": "no ack"} if McpErrors.code_of(response) == 1010 else response
	return {"sent": bool(_payload(response).get("sent", true))}
func _eval(params: Dictionary) -> Dictionary:
	var code := String(params.get("code", ""))
	if code.strip_edges().is_empty():
		return _fail(-32602, "missing required param 'code'",
			"Send the statements to run, e.g. {\"code\":\"return 1 + 1\"}.", {"param": "code"})
	if _game.is_empty(): return _fail(1008, "The game is not running.", HINT_RUN)
	var response := _request("eval", {"code": code}, TIMEOUT_EVAL_MS)
	if McpErrors.is_error(response):
		return response
	var payload := _payload(response)
	if not payload.has("result"): payload["result"] = null
	return payload
func _get_tree(params: Dictionary) -> Dictionary:
	if _game.is_empty(): return _fail(1008, "The game is not running.", HINT_RUN)
	var response := _request("get_tree", {"depth": int(params.get("depth", -1))}, TIMEOUT_DATA_MS)
	return response if McpErrors.is_error(response) else _payload(response)
func _get_node(params: Dictionary) -> Dictionary:
	if _game.is_empty(): return _fail(1008, "The game is not running.", HINT_RUN)
	var path := String(params.get("path", ""))
	if path.is_empty():
		return _fail(-32602, "missing required param 'path'",
			"Use \"/root\" for the tree root, otherwise an absolute node path.", {"param": "path"})
	var with_props := bool(params.get("with_props", true))
	var response := _request("get_node", {"path": path, "with_props": with_props}, TIMEOUT_DATA_MS)
	if McpErrors.is_error(response):
		return response
	var payload := _payload(response)
	if payload.is_empty():
		return _fail(1002, "the game reported no node for '%s'" % path, HINT_TREE, {"path": path})
	return payload

## Sends one request and blocks until the matching id arrives.
## Returns {"result": Variant} on success, or an McpErrors failure dictionary.
func _request(method: String, params: Dictionary, timeout_ms: int) -> Dictionary:
	var peer := _game_peer()
	if peer == null: return _fail(1008, "The game is not running.", HINT_RUN)
	_next_request += 1
	var request_id := "r%d" % _next_request
	var frame := {"id": request_id, "method": method, "params": params}
	if peer.send_text(JSON.stringify(frame)) != OK:
		_drop_game("send failed")
		return _fail(1008, "The game connection is broken.", HINT_RUN, {"method": method})
	var deadline := Time.get_ticks_msec() + timeout_ms
	while Time.get_ticks_msec() < deadline:
		_pump()
		if _pending.has(request_id):
			var answer: Dictionary = _pending.get(request_id, {})
			_pending.erase(request_id)
			if answer.has("error"):
				return _error_from_game(answer.get("error", null))
			return {"result": answer.get("result", {})}
		if _game.is_empty():
			return _fail(1008, "The game disconnected before answering '%s'." % method,
				HINT_RUN, {"method": method})
		OS.delay_msec(POLL_DELAY_MS)
	_pending.erase(request_id)
	return _fail(1010, "The game did not answer '%s' within %d ms" % [method, timeout_ms],
		"The game loop may be paused, blocked or stopped on a breakpoint.", {"method": method})
## Accepts new connections and drains every peer once.
func _pump() -> void:
	if _server == null:
		return
	var guard := 0
	while _server != null and _server.is_connection_available() and guard < MAX_PACKETS_PER_POLL:
		guard += 1
		_accept_one()
	for key in _peers.keys():
		_poll_peer(String(key))
## Blocks until an authenticated game shows up, or the budget is exhausted.
func _wait_for_game(timeout_ms: int) -> bool:
	var deadline := Time.get_ticks_msec() + timeout_ms
	while Time.get_ticks_msec() < deadline:
		_pump()
		if not _game.is_empty():
			return true
		OS.delay_msec(POLL_DELAY_MS)
	return not _game.is_empty()
## Normalises a game side error object into an McpErrors failure.
func _error_from_game(raw: Variant) -> Dictionary:
	if typeof(raw) != TYPE_DICTIONARY: return _fail(-32603, str(raw), "A game error payload was invalid.")
	var data: Dictionary = raw
	var context: Dictionary = {}
	var raw_context: Variant = data.get("context", {})
	if typeof(raw_context) == TYPE_DICTIONARY:
		context = raw_context
	return _fail(int(data.get("code", -32603)),
		String(data.get("message", "the game reported an error")), String(data.get("hint", "")), context)
## Extracts the result object of a successful response.
func _payload(response: Dictionary) -> Dictionary:
	var value: Variant = response.get("result", {})
	if typeof(value) == TYPE_DICTIONARY:
		return value
	return {} if value == null else {"value": value}

func _accept_one() -> void:
	if _server == null: return
	var connection := _server.take_connection()
	if connection == null: return
	var peer := WebSocketPeer.new()
	if peer.accept_stream(connection) != OK:
		_last_error = "accept_stream failed for an incoming game connection"
		push_warning("[MCP] runtime bridge " + _last_error)
		connection.disconnect_from_host()
		return
	while _peers.size() >= MAX_PEERS:
		var keys: Array = _peers.keys()
		if keys.is_empty():
			break
		_close_peer(String(keys[0]), "replaced by a newer game connection", CLOSE_SHUTDOWN)
	var peer_id := "g%d" % _next_peer
	_next_peer += 1
	_peers[peer_id] = {"peer": peer, "authed": false, "id": peer_id, "pid": 0,
		"last_seen": Time.get_ticks_msec()}
func _poll_peer(peer_id: String) -> void:
	var state: Dictionary = _peers.get(peer_id, {})
	if state.is_empty(): return
	var peer: WebSocketPeer = state.get("peer")
	if peer == null:
		_peers.erase(peer_id)
		return
	peer.poll()
	match peer.get_ready_state():
		WebSocketPeer.STATE_OPEN:
			state["last_seen"] = Time.get_ticks_msec()
			var guard := 0
			while peer.get_available_packet_count() > 0 and guard < MAX_PACKETS_PER_POLL:
				guard += 1
				var packet := peer.get_packet()
				if peer.was_string_packet() and not packet.is_empty():
					_handle_text(peer_id, packet.get_string_from_utf8())
		WebSocketPeer.STATE_CLOSED:
			_close_peer(peer_id, "game closed the connection (%d)" % peer.get_close_code(), -1)
## Parses one frame from the game: auth, response, or an ignored notification.
func _handle_text(peer_id: String, text: String) -> void:
	if text.is_empty(): return
	var parser := JSON.new()
	if parser.parse(text) != OK:
		_last_error = "runtime bridge received malformed JSON: %s" % parser.get_error_message()
		push_warning("[MCP] " + _last_error)
		return
	var data: Variant = parser.get_data()
	if typeof(data) != TYPE_DICTIONARY:
		return
	var message: Dictionary = data
	var state: Dictionary = _peers.get(peer_id, {})
	if state.is_empty():
		return
	if not bool(state.get("authed", false)):
		_handle_auth(peer_id, state, message)
		return
	var answer_id := str(message.get("id", ""))
	if answer_id.is_empty():
		return
	if message.has("error"):
		_pending[answer_id] = {"error": message.get("error")}
	else:
		_pending[answer_id] = {"result": message.get("result", {})}
## PROTOCOL.md §2 auth gate for the game process; a bad token closes with 4001.
func _handle_auth(peer_id: String, state: Dictionary, message: Dictionary) -> void:
	var frame_id := str(message.get("id", "0"))
	if String(message.get("method", "")) != "auth":
		_refuse(state, frame_id, "authentication required",
			"Send {\"method\":\"auth\",\"params\":{\"token\":\"...\"}} first.")
		_close_peer(peer_id, "auth required", CLOSE_AUTH_FAILED)
		return
	var params: Dictionary = {}
	var raw_params: Variant = message.get("params", {})
	if typeof(raw_params) == TYPE_DICTIONARY:
		params = raw_params
	var expected := _token()
	if not expected.is_empty() and String(params.get("token", "")) != expected:
		_last_error = "runtime bridge rejected a game connection (bad token)"
		push_warning("[MCP] " + _last_error)
		_refuse(state, frame_id, "invalid token", "Read the token from .godot/mcp_bridge.json.")
		_close_peer(peer_id, "auth failed", CLOSE_AUTH_FAILED)
		return
	state["authed"] = true
	state["pid"] = int(params.get("pid", 0))
	state["last_seen"] = Time.get_ticks_msec()
	_game = state
	_game_cursor = 0
	_send_frame(state, {"jsonrpc": "2.0", "id": frame_id, "result": {"ok": true, "client_id": peer_id}})
	print("[MCP] game connected (pid %d)" % int(state.get("pid", 0)))
## Writes a 1011 failure envelope to a peer that is not authenticated yet.
func _refuse(state: Dictionary, frame_id: String, message: String, hint: String) -> void:
	_send_frame(state, {"jsonrpc": "2.0", "id": frame_id,
		"error": {"code": 1011, "message": message, "hint": hint, "context": {}}})
func _send_frame(state: Dictionary, payload: Dictionary) -> void:
	var peer: WebSocketPeer = state.get("peer")
	if peer == null or peer.get_ready_state() != WebSocketPeer.STATE_OPEN:
		return
	if peer.send_text(JSON.stringify(payload)) != OK:
		_last_error = "runtime bridge failed to write a frame"
		push_warning("[MCP] " + _last_error)
func _close_peer(peer_id: String, reason: String, code: int) -> void:
	var state: Dictionary = _peers.get(peer_id, {})
	_peers.erase(peer_id)
	if String(_game.get("id", "")) == peer_id:
		_game = {}
		_game_cursor = 0
	var peer: WebSocketPeer = state.get("peer")
	if peer == null:
		return
	if peer.get_ready_state() == WebSocketPeer.STATE_OPEN:
		peer.close(code if code > 0 else CLOSE_SHUTDOWN, reason)
	print("[MCP] game connection %s closed (%s)" % [peer_id, reason])
## Forgets every game peer (used by run/stop, never by a plain poll).
func _drop_game(reason: String) -> void:
	for key in _peers.keys():
		_close_peer(String(key), reason, CLOSE_SHUTDOWN)
	_peers.clear()
	_game = {}
	_pending.clear()
	_game_cursor = 0
func _game_peer() -> WebSocketPeer:
	if _game.is_empty():
		return null
	var peer: WebSocketPeer = _game.get("peer")
	if peer == null or peer.get_ready_state() != WebSocketPeer.STATE_OPEN:
		return null
	return peer

## Pulls new records from the game buffer and the debugger capture.
func _harvest_logs() -> void:
	if not _game.is_empty():
		var response := _request("logs", {"since": _game_cursor, "limit": 200}, TIMEOUT_DATA_MS)
		if not McpErrors.is_error(response):
			var payload := _payload(response)
			_collect(payload.get("entries", []))
			_game_cursor = maxi(_game_cursor, int(payload.get("next", _game_cursor)))
	if _capture == null: return
	var captured: Variant = _capture.call("get_logs", _capture_cursor, "", 200)
	if typeof(captured) != TYPE_DICTIONARY:
		return
	var data: Dictionary = captured
	_collect(data.get("entries", []))
	_capture_cursor = maxi(_capture_cursor, int(data.get("next", _capture_cursor)))
## Appends every {level, ts, text} record of `entries` to the merged buffer.
func _collect(entries: Variant) -> void:
	if typeof(entries) != TYPE_ARRAY:
		return
	for item in entries:
		if typeof(item) != TYPE_DICTIONARY:
			continue
		var record: Dictionary = item
		var text := String(record.get("text", ""))
		if text.strip_edges().is_empty():
			continue
		_merged_seq += 1
		var ts := int(record.get("ts", 0))
		_merged.append({"seq": _merged_seq, "level": String(record.get("level", "info")),
			"ts": ts if ts > 0 else int(Time.get_unix_time_from_system()), "text": text})
	while _merged.size() > MAX_MERGED: _merged.pop_front()

## Registers the stdout/stderr capture once, guarded for every Godot version.
func _ensure_capture() -> void:
	if _capture != null or host == null:
		return
	var plugin: Object = host
	# plugin.gd may own the capture through a `debugger` property.
	if "debugger" in plugin:
		var existing: Variant = plugin.get("debugger")
		if typeof(existing) == TYPE_OBJECT and existing != null:
			var candidate: Object = existing
			if candidate.has_method("get_logs"):
				_capture = candidate
				return
	if not ClassDB.class_exists("EditorDebuggerPlugin") or not ResourceLoader.exists(CAPTURE_PATH):
		return
	var resource: Resource = load(CAPTURE_PATH)
	if resource == null or not (resource is Script):
		push_warning("[MCP] debugger capture is not a script: " + CAPTURE_PATH)
		return
	_capture = (resource as Script).new()
	if _capture != null and plugin.has_method("add_debugger_plugin"):
		plugin.call("add_debugger_plugin", _capture)

## EditorInterface singleton, with a fallback on the owning plugin.
func _editor() -> Object:
	var plugin: Object = host
	if plugin != null and plugin.has_method("get_editor_interface"):
		var direct: Variant = plugin.call("get_editor_interface")
		if typeof(direct) == TYPE_OBJECT and direct != null:
			return direct
	return Engine.get_singleton("EditorInterface")
func _is_playing() -> bool:
	if not Engine.is_editor_hint():
		return false
	var editor := _editor()
	if editor == null or not editor.has_method("is_playing_scene"):
		return false
	return bool(editor.call("is_playing_scene"))
func _playing_scene() -> String:
	if not Engine.is_editor_hint():
		return ""
	var editor := _editor()
	if editor == null or not editor.has_method("get_playing_scene"):
		return ""
	return String(editor.call("get_playing_scene"))
func _token() -> String:
	var plugin: Object = host
	if plugin != null and plugin.has_method("get_token"):
		return String(plugin.call("get_token"))
	return ""
func _fail(code: int, message: String, hint: String = "", context: Dictionary = {}) -> Dictionary:
	return McpErrors.fail(code, message, hint, context)
