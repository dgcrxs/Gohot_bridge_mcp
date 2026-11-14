@tool
extends RefCounted

## McpWsServer - WebSocket transport for the Godot Bridge MCP editor plugin.
##
## One TCPServer bound to 127.0.0.1, any number of WebSocketPeer clients, one
## JSON-RPC frame per text packet. Polling based: the plugin calls poll() from
## EditorPlugin._process().
##
## Responsibilities (PROTOCOL.md §1, §2):
##   * accept/close peers, per-peer state {peer, authed, id, last_seen, idle}
##   * auth gate: the first frame must be `auth` (only `ping` is tolerated
##     before auth, because port discovery relies on it)
##   * heartbeat: ping every 5 s, close peers idle for 30 s
##   * JSON parse errors answer -32700 without breaking the poll loop
##   * ring buffer of log entries exposed through log.list / log.clear
##
## Never let a malformed frame escape: every entry point validates types and
## reports the failure through the error envelope.

const McpErrors = preload("res://addons/godot_mcp/core/errors.gd")

const PROTOCOL_VERSION := "2.0"
const BIND_ADDRESS := "127.0.0.1"
const HEARTBEAT_INTERVAL_MS := 5000
const IDLE_TIMEOUT_MS := 30000
const MAX_LOG_ENTRIES := 500
const MAX_PACKETS_PER_POLL := 64
const CLOSE_IDLE := 4000
const CLOSE_AUTH_FAILED := 4001
const CLOSE_SERVER_SHUTDOWN := 1001

## Owning EditorPlugin (provides get_status()/log_event()).
var host: Variant = null
## McpRouter instance, injected by plugin.gd.
var router: Variant = null
## Shared secret. An empty token disables authentication entirely.
var token := ""
## Port the server is listening on, 0 while stopped.
var port := 0
## Kept for PROTOCOL.md §2 port discovery, which probes with an unauthenticated ping.
var allow_ping_preauth := true

var _server: TCPServer = null
var _peers: Dictionary = {}
var _closing: Array = []
var _next_client := 1
var _logs: Array = []
var _log_seq := 0
var _running := false
var _last_heartbeat := 0
var _last_error := ""


func _init(plugin_host: Variant = null) -> void:
	host = plugin_host
	_last_heartbeat = Time.get_ticks_msec()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

## Starts listening on `bind_port`.
##
## Returns:
##   The port actually bound, or -1 on failure.
func start(bind_port: int) -> int:
	stop()
	if bind_port <= 0 or bind_port > 65535:
		_last_error = "invalid port %d" % bind_port
		push_error("[MCP] " + _last_error)
		return -1
	var server := TCPServer.new()
	var error := server.listen(bind_port, BIND_ADDRESS)
	if error != OK:
		_last_error = "cannot listen on %s:%d (err %d)" % [BIND_ADDRESS, bind_port, error]
		push_error("[MCP] " + _last_error)
		append_log({"level": "error", "method": "server.start", "ok": false, "error": _last_error})
		return -1
	_server = server
	port = bind_port
	_running = true
	_last_heartbeat = Time.get_ticks_msec()
	_last_error = ""
	print("[MCP] WebSocket server listening on %s:%d (protocol %s)" % [BIND_ADDRESS, bind_port, PROTOCOL_VERSION])
	append_log({"level": "info", "method": "server.start", "ok": true, "error": ""})
	return port


## Closes every peer and stops listening.
func stop() -> void:
	for client_id in _peers.keys():
		_close_peer(client_id, "server shutdown", CLOSE_SERVER_SHUTDOWN)
	_closing.clear()
	_peers.clear()
	if _server != null:
		_server.stop()
		_server = null
	if _running:
		print("[MCP] WebSocket server stopped")
		append_log({"level": "info", "method": "server.stop", "ok": true, "error": ""})
	_running = false
	port = 0


## Advances the transport state machine. Safe to call every frame.
func poll() -> void:
	_poll_closing()
	if not _running or _server == null:
		return
	_accept_pending()
	if _peers.is_empty():
		return
	var now := Time.get_ticks_msec()
	for client_id in _peers.keys():
		_poll_peer(String(client_id), now)
	_heartbeat(now)


## True while the TCP listener is alive.
func is_running() -> bool:
	return _running


## Number of live, accepted clients.
func client_count() -> int:
	return _peers.size()


## Last transport level error message ("" when none).
func last_error() -> String:
	return _last_error


# ---------------------------------------------------------------------------
# Accept / poll peers
# ---------------------------------------------------------------------------

## Accepts every pending TCP connection.
func _accept_pending() -> void:
	var guard := 0
	while _server != null and _server.is_connection_available() and guard < MAX_PACKETS_PER_POLL:
		guard += 1
		var connection := _server.take_connection()
		if connection == null:
			continue
		var peer := WebSocketPeer.new()
		var error := peer.accept_stream(connection)
		if error != OK:
			_last_error = "accept_stream failed (err %d)" % error
			push_error("[MCP] " + _last_error)
			connection.disconnect_from_host()
			continue
		var client_id := "c%d" % _next_client
		_next_client += 1
		_peers[client_id] = {
			"peer": peer,
			"authed": token.is_empty(),
			"id": client_id,
			"last_seen": Time.get_ticks_msec(),
			"idle": false,
		}
		append_log({"level": "info", "method": "client.connect", "ok": true, "error": "", "client_id": client_id})


## Polls one peer, reads its packets and drops it when closed.
func _poll_peer(client_id: String, _now: int) -> void:
	var state: Dictionary = _peers.get(client_id, {})
	var peer: WebSocketPeer = state.get("peer")
	if peer == null:
		_peers.erase(client_id)
		return
	peer.poll()
	match peer.get_ready_state():
		WebSocketPeer.STATE_OPEN:
			_read_packets(client_id, peer)
		WebSocketPeer.STATE_CLOSING:
			pass
		WebSocketPeer.STATE_CLOSED:
			var code := peer.get_close_code()
			var reason := peer.get_close_reason()
			_close_peer(client_id, "closed by client (%d %s)" % [code, reason], -1)


## Drains the packet queue of one peer.
func _read_packets(client_id: String, peer: WebSocketPeer) -> void:
	var guard := 0
	while peer.get_available_packet_count() > 0 and guard < MAX_PACKETS_PER_POLL:
		guard += 1
		var packet := peer.get_packet()
		if packet.is_empty():
			continue
		if not peer.was_string_packet():
			append_log({"level": "warn", "method": "client.binary", "ok": false, "error": "binary frame ignored"})
			continue
		var state: Dictionary = _peers.get(client_id, {})
		if state.is_empty():
			return
		state["last_seen"] = Time.get_ticks_msec()
		state["idle"] = false
		_handle_text(client_id, state, packet.get_string_from_utf8())


## Pings authenticated peers and closes idle ones (PROTOCOL.md §1).
func _heartbeat(now: int) -> void:
	if now - _last_heartbeat < HEARTBEAT_INTERVAL_MS:
		return
	_last_heartbeat = now
	for client_id in _peers.keys():
		var state: Dictionary = _peers.get(client_id, {})
		var peer: WebSocketPeer = state.get("peer")
		if peer == null:
			continue
		var idle_ms := now - int(state.get("last_seen", now))
		if idle_ms > IDLE_TIMEOUT_MS:
			state["idle"] = true
			_close_peer(String(client_id), "idle for %d ms" % idle_ms, CLOSE_IDLE)
			continue
		# WebSocketPeer exposes no ping(); liveness is tracked from inbound
		# frames. The Python bridge sends a `ping` RPC every 10 s, which resets
		# last_seen well inside IDLE_TIMEOUT_MS.


## Moves a peer into the closing list so the close frame can be flushed.
func _close_peer(client_id: String, reason: String, code: int) -> void:
	var state: Dictionary = _peers.get(client_id, {})
	_peers.erase(client_id)
	var peer: WebSocketPeer = state.get("peer")
	if peer == null:
		return
	if peer.get_ready_state() == WebSocketPeer.STATE_OPEN:
		peer.close(code if code > 0 else CLOSE_SERVER_SHUTDOWN, reason)
		_closing.append(peer)
	append_log({"level": "info", "method": "client.disconnect", "ok": true, "error": "%s: %s" % [client_id, reason]})
	print("[MCP] client %s closed (%s)" % [client_id, reason])


## Polls peers that are shutting down and forgets them once closed.
func _poll_closing() -> void:
	if _closing.is_empty():
		return
	var remaining: Array = []
	for peer in _closing:
		var socket: WebSocketPeer = peer
		if socket == null:
			continue
		socket.poll()
		if socket.get_ready_state() != WebSocketPeer.STATE_CLOSED:
			remaining.append(socket)
	_closing = remaining


# ---------------------------------------------------------------------------
# Frame handling
# ---------------------------------------------------------------------------

## Parses and dispatches a single text frame. Any failure becomes an error
## response; the poll loop is never interrupted.
func _handle_text(client_id: String, state: Dictionary, text: String) -> void:
	var parser := JSON.new()
	if parser.parse(text) != OK:
		_send_error(state, null, McpErrors.ERR_PARSE, "JSON parse error: %s" % parser.get_error_message(), "Send one JSON object per frame.", {"line": parser.get_error_line()})
		return
	var data: Variant = parser.get_data()
	if typeof(data) != TYPE_DICTIONARY:
		_send_error(state, null, McpErrors.ERR_INVALID_REQUEST, "request must be a JSON object", "Example: {\"jsonrpc\":\"2.0\",\"id\":\"1\",\"method\":\"ping\"}")
		return
	var request: Dictionary = data
	var request_id: Variant = request.get("id", null)
	var method_value: Variant = request.get("method", null)
	if typeof(method_value) != TYPE_STRING or String(method_value).is_empty():
		_send_error(state, request_id, McpErrors.ERR_INVALID_REQUEST, "missing 'method'", "Every frame needs a method name.")
		return
	var method := String(method_value)
	var params_value: Variant = request.get("params", {})
	if params_value == null:
		params_value = {}
	if typeof(params_value) != TYPE_DICTIONARY:
		_send_error(state, request_id, McpErrors.ERR_INVALID_PARAMS, "'params' must be an object", "Send \"params\": {} when there are no arguments.")
		return
	var params: Dictionary = params_value
	# `auth` is always handled by the transport, so a re-auth is validated too.
	if method == "auth":
		_handle_auth(client_id, state, request_id, params)
		return
	if not bool(state.get("authed", false)):
		if not _handle_preauth(client_id, state, request_id, method):
			return
	_dispatch(state, request_id, method, params)


## Enforces the auth gate.
##
## Returns:
##   true when the frame may continue to the router.
func _handle_preauth(client_id: String, state: Dictionary, request_id: Variant, method: String) -> bool:
	if method == "ping" and allow_ping_preauth:
		return true
	_last_error = "unauthorised method '%s'" % method
	_send_error(state, request_id, McpErrors.ERR_AUTH_FAILED, "authentication required", "Send {\"method\":\"auth\",\"params\":{\"token\":\"...\"}} first.", {"method": method})
	_close_peer(client_id, "auth required", CLOSE_AUTH_FAILED)
	return false


## Validates the token and marks the peer as authenticated.
func _handle_auth(client_id: String, state: Dictionary, request_id: Variant, params: Dictionary) -> void:
	var provided := String(params.get("token", ""))
	if token.is_empty() or provided == token:
		state["authed"] = true
		append_log({"level": "info", "method": "auth", "ok": true, "error": ""})
		_send(state, {
			"jsonrpc": "2.0",
			"id": request_id,
			"result": {"ok": true, "client_id": client_id},
			"elapsed_ms": 0,
		})
		return
	_last_error = "auth failed for client %s" % client_id
	append_log({"level": "error", "method": "auth", "ok": false, "error": _last_error})
	_send_error(state, request_id, McpErrors.ERR_AUTH_FAILED, "invalid token", "Read the token from .godot/mcp_bridge.json.")
	_close_peer(client_id, "auth failed", CLOSE_AUTH_FAILED)


## Forwards the request to the router and writes the response.
func _dispatch(state: Dictionary, request_id: Variant, method: String, params: Dictionary) -> void:
	if router == null:
		_send_error(state, request_id, McpErrors.ERR_INTERNAL, "router is not initialised", "Re-enable the Godot Bridge MCP plugin.")
		return
	var client_id := String(state.get("id", ""))
	var response: Variant = router.call("dispatch", method, params, client_id)
	if typeof(response) != TYPE_DICTIONARY:
		_send_error(state, request_id, McpErrors.ERR_INTERNAL, "router returned a non-object response", "", {"method": method})
		return
	var payload: Dictionary = response
	var envelope := {"jsonrpc": "2.0", "id": request_id}
	if payload.has("error"):
		envelope["error"] = payload["error"]
		envelope["elapsed_ms"] = int(payload.get("elapsed_ms", 0))
	elif McpErrors.is_error(payload.get("result", null)):
		envelope["error"] = McpErrors.payload(payload["result"])
		envelope["elapsed_ms"] = int(payload.get("elapsed_ms", 0))
	else:
		var result: Variant = payload.get("result", {})
		envelope["result"] = {} if result == null else result
		envelope["elapsed_ms"] = int(payload.get("elapsed_ms", 0))
	_send(state, envelope)


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

## Serialises and writes one frame, ignoring peers that already closed.
func _send(state: Dictionary, payload: Dictionary) -> void:
	var peer: WebSocketPeer = state.get("peer")
	if peer == null:
		return
	if peer.get_ready_state() != WebSocketPeer.STATE_OPEN:
		return
	var error := peer.send_text(JSON.stringify(payload))
	if error != OK:
		_last_error = "send_text failed (err %d)" % error
		push_error("[MCP] " + _last_error)


## Writes an error envelope (PROTOCOL.md §2 failure shape).
func _send_error(state: Dictionary, request_id: Variant, code: int, message: String, hint: String = "", context: Dictionary = {}) -> void:
	var failure := McpErrors.make(code, message, hint, context)
	var payload := McpErrors.payload(failure)
	_send(state, {
		"jsonrpc": "2.0",
		"id": request_id,
		"error": payload,
		"elapsed_ms": 0,
	})


# ---------------------------------------------------------------------------
# Log ring buffer
# ---------------------------------------------------------------------------

## Appends one normalised entry, keeping at most MAX_LOG_ENTRIES.
func append_log(entry: Dictionary) -> void:
	_log_seq += 1
	var record := {
		"seq": _log_seq,
		"ts": int(entry.get("ts", int(Time.get_unix_time_from_system()))),
		"level": String(entry.get("level", "info")),
		"method": String(entry.get("method", "")),
		"ok": bool(entry.get("ok", true)),
		"ms": int(entry.get("ms", 0)),
		"error": String(entry.get("error", "")),
		"client_id": String(entry.get("client_id", "")),
	}
	_logs.append(record)
	while _logs.size() > MAX_LOG_ENTRIES:
		_logs.pop_front()


## Returns log entries newer than `since` (PROTOCOL.md §3.1 log.list).
##
## Returns:
##   {"entries": [{seq,ts,level,method,ok,ms,error}], "next": int}
func get_logs(since: int = 0, level: String = "", limit: int = 200) -> Dictionary:
	var entries: Array = []
	for record in _logs:
		var item: Dictionary = record
		if int(item.get("seq", 0)) <= since:
			continue
		if not level.is_empty() and String(item.get("level", "")) != level:
			continue
		entries.append(item.duplicate(true))
	var bounded := limit if limit > 0 else 200
	if entries.size() > bounded:
		entries = entries.slice(entries.size() - bounded)
	return {"entries": entries, "next": _log_seq}


## Drops every log entry.
##
## Returns:
##   Number of entries removed.
func clear_logs() -> int:
	var cleared := _logs.size()
	_logs.clear()
	return cleared
