@tool
extends RefCounted

## McpHandshake - token generation, port probing and handshake file handling.
##
## PROTOCOL.md §1: the plugin writes `<project>/.godot/mcp_bridge.json` on start so
## the Python server can discover port/token without any manual configuration.
##
## All helpers are static; the module is used through
##   const McpHandshake = preload("res://addons/godot_mcp/core/handshake.gd")

## ProjectSettings keys (PROTOCOL.md §3.1 get_config / set_config).
const SETTING_TOKEN := "mcp_bridge/auth_token"
const SETTING_PORT := "mcp_bridge/port"
const SETTING_AUTO_START := "mcp_bridge/auto_start"

const DEFAULT_PORT := 8765
const PROTOCOL_VERSION := "2.0"
const SCHEMA_VERSION := 1

## 24 random bytes -> 48 hex chars, as required by PROTOCOL.md §1.
const TOKEN_BYTES := 24

const HANDSHAKE_RES_PATH := "res://.godot/mcp_bridge.json"
const HANDSHAKE_USER_PATH := "user://mcp_bridge.json"


## Installs a setting with a default value and editor-friendly metadata.
##
## Args:
##   key: ProjectSettings key.
##   default_value: Value used when the project has no override.
##   info: Extra property info entries (e.g. hint / hint_string).
static func register_setting(key: String, default_value: Variant, info: Dictionary = {}) -> void:
	if ProjectSettings.has_setting(key):
		return
	ProjectSettings.set_setting(key, default_value)
	ProjectSettings.set_initial_value(key, default_value)
	var property_info := info.duplicate(true)
	property_info["name"] = key
	if not property_info.has("type"):
		property_info["type"] = typeof(default_value)
	ProjectSettings.add_property_info(property_info)


## Returns the persisted token, generating one on first run.
##
## An explicitly empty token means "auth disabled" and is preserved, so a project
## can opt out of authentication on purpose (PROTOCOL.md §2).
static func ensure_token() -> String:
	if ProjectSettings.has_setting(SETTING_TOKEN):
		var existing := String(ProjectSettings.get_setting(SETTING_TOKEN, ""))
		if not existing.is_empty():
			return existing
		# Explicit opt-out: keep it empty so the server runs without auth.
		print("[MCP] auth token is empty, authentication is disabled")
		return ""
	var token := _generate_token()
	ProjectSettings.set_setting(SETTING_TOKEN, token)
	ProjectSettings.set_initial_value(SETTING_TOKEN, "")
	ProjectSettings.add_property_info({
		"name": SETTING_TOKEN,
		"type": TYPE_STRING,
		"hint": PROPERTY_HINT_NONE,
		"hint_string": "",
	})
	ProjectSettings.save()
	print("[MCP] auth token generated and stored in project settings")
	return token


## Returns the configured token without creating one.
static func get_token() -> String:
	if not ProjectSettings.has_setting(SETTING_TOKEN):
		return ""
	return String(ProjectSettings.get_setting(SETTING_TOKEN, ""))


## True when the editor must authenticate incoming clients.
static func requires_auth() -> bool:
	return not get_token().is_empty()


## Finds a free TCP port starting at `start_port` (PROTOCOL.md §1: +0..+19).
##
## Args:
##   start_port: First candidate port.
##   max_tries: Number of consecutive ports to probe.
##
## Returns:
##   The first free port, or -1 when every candidate is busy.
static func pick_port(start_port: int, max_tries: int = 20) -> int:
	if start_port <= 0 or start_port > 65535:
		return -1
	var tries := max_tries if max_tries > 0 else 1
	for offset in range(tries):
		var candidate := start_port + offset
		if candidate > 65535:
			break
		var probe := TCPServer.new()
		var error := probe.listen(candidate, "127.0.0.1")
		probe.stop()
		probe = null
		if error == OK:
			return candidate
	return -1


## Writes the handshake file and returns its absolute path.
##
## Falls back to `user://mcp_bridge.json` when the project directory is not
## writable (exported builds, read-only mounts).
static func write_handshake(port: int, runtime_port: int) -> String:
	var absolute := ProjectSettings.globalize_path(HANDSHAKE_RES_PATH)
	var directory := absolute.get_base_dir()
	if not DirAccess.dir_exists_absolute(directory):
		var dir_error := DirAccess.make_dir_recursive_absolute(directory)
		if dir_error != OK:
			push_warning("[MCP] cannot create %s (err %d)" % [directory, dir_error])
	var error := _write_json(absolute, _handshake_payload(port, runtime_port))
	if error == OK:
		print("[MCP] handshake written to %s" % absolute)
		return absolute
	push_warning("[MCP] cannot write %s (err %d), falling back to %s" % [absolute, error, HANDSHAKE_USER_PATH])
	var fallback := ProjectSettings.globalize_path(HANDSHAKE_USER_PATH)
	var fallback_error := _write_json(fallback, _handshake_payload(port, runtime_port))
	if fallback_error != OK:
		push_error("[MCP] cannot write handshake file (err %d)" % fallback_error)
		return ""
	return fallback


## Removes the handshake files so a stopped editor is never discovered.
static func clear_handshake() -> void:
	var paths := [ProjectSettings.globalize_path(HANDSHAKE_RES_PATH), ProjectSettings.globalize_path(HANDSHAKE_USER_PATH)]
	for path in paths:
		if FileAccess.file_exists(path):
			var error := DirAccess.remove_absolute(path)
			if error != OK and error != ERR_FILE_NOT_FOUND:
				push_warning("[MCP] cannot remove %s (err %d)" % [path, error])


## Absolute project directory without a trailing separator.
static func get_project_root() -> String:
	var path := ProjectSettings.globalize_path("res://")
	while path.ends_with("/") and path.length() > 1:
		path = path.substr(0, path.length() - 1)
	if path.is_empty():
		path = OS.get_executable_path().get_base_dir()
	return path


## Builds the exact payload required by PROTOCOL.md §1.
static func _handshake_payload(port: int, runtime_port: int) -> Dictionary:
	var version_info := Engine.get_version_info()
	return {
		"schema": SCHEMA_VERSION,
		"protocol": PROTOCOL_VERSION,
		"port": port,
		"runtime_port": runtime_port,
		"token": get_token(),
		"pid": OS.get_process_id(),
		"project_path": get_project_root(),
		"project_name": String(ProjectSettings.get_setting("application/config/name", "")),
		"godot_version": String(version_info.get("string", "unknown")),
		"started_at": int(Time.get_unix_time_from_system()),
	}


## Writes pretty-printed JSON, creating the parent directory when needed.
static func _write_json(path: String, data: Dictionary) -> int:
	var file := FileAccess.open(path, FileAccess.WRITE)
	if file == null:
		return FileAccess.get_open_error()
	file.store_string(JSON.stringify(data, "  "))
	file.close()
	return OK


## 24 cryptographically random bytes, hex encoded.
static func _generate_token() -> String:
	var crypto := Crypto.new()
	var bytes := crypto.generate_random_bytes(TOKEN_BYTES)
	return bytes.hex_encode()
