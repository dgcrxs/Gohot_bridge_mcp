@tool
extends RefCounted

## McpRouter - JSON-RPC method router for the Godot Bridge MCP editor plugin.
##
## Maps "group.action" method names onto the ops modules (PROTOCOL.md §3), keeps
## the eight system methods local, and translates the v1 aliases (§3.11).
##
## Module contract (all modules are plain RefCounted scripts):
##   * constructed with the plugin instance (`_init(host)`) or configured later
##     through an optional `setup(host)`
##   * expose `handle(full_method: String, params: Dictionary) -> Variant` OR
##     one method per action (`get_tree(params)`)
##   * return the `result` payload directly, or `McpErrors.make(...)` on failure
##
## dispatch() always returns {"result": ...} or {"error": {...}} plus elapsed_ms,
## so the transport layer never needs to know about error wrapping.

const McpErrors = preload("res://addons/godot_mcp/core/errors.gd")
const McpHandshake = preload("res://addons/godot_mcp/core/handshake.gd")
const McpEditorState = preload("res://addons/godot_mcp/core/editor_state.gd")

const PROTOCOL_VERSION := "2.0"
const PLUGIN_VERSION := "2.0.0"

const SETTING_AUTO_START := "mcp_bridge/auto_start"
const SETTING_PORT := "mcp_bridge/port"
const DEFAULT_PORT := 8765

## group -> owning module key.
const GROUP_MODULE := {
	"scene": "editor",
	"node": "editor",
	"prop": "editor",
	"signal": "editor",
	"editor": "editor",
	"script": "resource",
	"res": "resource",
	"file": "resource",
	"fs": "resource",
	"docs": "docs",
	"execute": "execute",
	"runtime": "runtime",
}

## module key -> candidate script paths (owned by the other engineers).
## The first existing path wins, so a module can live in core/ or ops/.
const MODULE_PATHS := {
	"editor": ["res://addons/godot_mcp/core/editor_ops.gd", "res://addons/godot_mcp/ops/editor_ops.gd"],
	"resource": ["res://addons/godot_mcp/core/resource_ops.gd", "res://addons/godot_mcp/ops/resource_ops.gd"],
	"docs": ["res://addons/godot_mcp/core/docs_api.gd", "res://addons/godot_mcp/ops/docs_api.gd"],
	"execute": ["res://addons/godot_mcp/core/execute_ops.gd", "res://addons/godot_mcp/ops/execute_ops.gd"],
	"runtime": ["res://addons/godot_mcp/core/runtime_ops.gd", "res://addons/godot_mcp/ops/runtime_ops.gd"],
}

## v1 method -> v2 method plus parameter renames (PROTOCOL.md §3.11).
const LEGACY_MAP := {
	"get_scene_tree": {"method": "scene.get_tree", "rename": {}},
	"add_node": {"method": "node.add", "rename": {"parent": "parent_path", "node_type": "type"}},
	"get_node_properties": {"method": "prop.get_all", "rename": {"node_path": "path"}},
	"set_node_property": {"method": "prop.set", "rename": {"node_path": "path", "prop": "property", "name": "property"}},
	"execute_script": {"method": "execute.gdscript", "rename": {}},
	"get_selected_nodes": {"method": "editor.get_selection", "rename": {}},
	"get_editor_info": {"method": "get_editor_info", "rename": {}},
	"list_node_types": {"method": "docs.search", "rename": {}},
}

const SYSTEM_METHODS := [
	"ping",
	"auth",
	"get_capabilities",
	"get_editor_info",
	"get_config",
	"set_config",
	"log.list",
	"log.clear",
]

const GROUPS := [
	"system",
	"scene",
	"node",
	"prop",
	"signal",
	"script",
	"res",
	"file",
	"fs",
	"editor",
	"runtime",
	"docs",
	"execute",
	"legacy",
]

const METHODS := [
	# system (§3.1)
	"ping", "auth", "get_capabilities", "get_editor_info", "get_config", "set_config", "log.list", "log.clear",
	# scene (§3.2)
	"scene.get_tree", "scene.current", "scene.list_open", "scene.open", "scene.save", "scene.save_as", "scene.create",
	# node (§3.3)
	"node.get", "node.add", "node.remove", "node.rename", "node.move", "node.duplicate", "node.find", "node.call",
	# property (§3.4)
	"prop.list", "prop.get", "prop.get_all", "prop.set", "prop.set_many", "prop.reset",
	# signal (§3.5)
	"signal.list", "signal.connect", "signal.disconnect",
	# script (§3.6)
	"script.read", "script.write", "script.create", "script.delete", "script.attach", "script.detach", "script.check", "script.list", "script.search",
	# resource / filesystem (§3.7)
	"res.list", "res.info", "res.load", "res.create", "res.duplicate", "res.delete", "res.dependencies", "res.reimport", "file.read", "file.write", "fs.search", "fs.list_dir",
	# editor (§3.8)
	"editor.get_selection", "editor.set_selection", "editor.undo", "editor.redo", "editor.undo_history", "editor.inspect", "editor.open_script", "editor.project_settings", "editor.set_project_setting", "editor.autoloads",
	# runtime (§3.9)
	"runtime.status", "runtime.run", "runtime.stop", "runtime.logs", "runtime.screenshot", "runtime.input", "runtime.eval", "runtime.get_tree", "runtime.get_node",
	# docs (§3.10) + execute (§3.12)
	"docs.class", "docs.search", "execute.gdscript",
	# legacy (§3.11)
	"get_scene_tree", "add_node", "get_node_properties", "set_node_property", "execute_script", "get_selected_nodes", "list_node_types",
]

## Owning EditorPlugin. Provides runtime ops, token, config and logging.
var host: Variant = null

var _modules: Dictionary = {}


func _init(plugin_host: Variant = null) -> void:
	host = plugin_host


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

## Routes one request and measures its duration.
##
## Returns:
##   {"result": Variant, "elapsed_ms": int} or {"error": {...}, "elapsed_ms": int}
func dispatch(method: String, params: Dictionary, client_id: String = "") -> Dictionary:
	var started := Time.get_ticks_usec()
	var response := _route(method, params, client_id)
	var elapsed := int((Time.get_ticks_usec() - started) / 1000)
	response["elapsed_ms"] = elapsed
	_log_call(method, response, elapsed, client_id)
	return response


## Chooses between system methods, legacy aliases and module methods.
func _route(method: String, params: Dictionary, client_id: String) -> Dictionary:
	if method.is_empty():
		return _fail(McpErrors.ERR_INVALID_REQUEST, "missing method", "Send {\"method\":\"ping\"}.")
	if SYSTEM_METHODS.has(method):
		return _handle_system(method, params, client_id)
	if method.find(".") <= 0:
		return _route_legacy(method, params, client_id)
	return _route_module(method, params)


## Translates a v1 alias and re-uses the normal dispatch path.
func _route_legacy(method: String, params: Dictionary, client_id: String) -> Dictionary:
	if not LEGACY_MAP.has(method):
		return _fail(McpErrors.ERR_METHOD_NOT_FOUND, "unknown method '%s'" % method, "Call godot_get_capabilities for the method list.", {"method": method})
	var alias: Dictionary = LEGACY_MAP[method]
	var target := String(alias.get("method", ""))
	var mapped := _map_legacy_params(params, alias.get("rename", {}))
	if method == "list_node_types":
		mapped["query"] = ""
		if not mapped.has("instantiable_only"):
			mapped["instantiable_only"] = true
	if SYSTEM_METHODS.has(target):
		return _handle_system(target, mapped, client_id)
	return _route_module(target, mapped)


## Applies v1 -> v2 parameter renames without overwriting explicit v2 keys.
func _map_legacy_params(params: Dictionary, renames: Variant) -> Dictionary:
	var mapped := params.duplicate(true)
	if typeof(renames) != TYPE_DICTIONARY:
		return mapped
	var table: Dictionary = renames
	for old_key in table.keys():
		var new_key := String(table[old_key])
		if mapped.has(old_key) and not mapped.has(new_key):
			mapped[new_key] = mapped[old_key]
	return mapped


## Dispatches to the ops module that owns the method group.
func _route_module(method: String, params: Dictionary) -> Dictionary:
	var dot := method.find(".")
	var group := method.substr(0, dot)
	var action := method.substr(dot + 1)
	if not GROUP_MODULE.has(group):
		return _fail(McpErrors.ERR_METHOD_NOT_FOUND, "unknown method group '%s'" % group, "Call godot_get_capabilities for the method list.", {"method": method})
	var module := _module(String(GROUP_MODULE[group]))
	if module == null:
		return _fail(McpErrors.ERR_UNSUPPORTED, "method group '%s' is unavailable in this build" % group, "The %s module could not be loaded." % group, {"method": method})
	return _invoke(module, method, action, params)


## Calls the module entry point and normalises its return value.
func _invoke(module: Object, method: String, action: String, params: Dictionary) -> Dictionary:
	var entry := ""
	if module.has_method("handle"):
		entry = "handle"
	elif module.has_method(action):
		entry = action
	else:
		var snake := action.replace(".", "_")
		if module.has_method(snake):
			entry = snake
	if entry.is_empty():
		return _fail(McpErrors.ERR_METHOD_NOT_FOUND, "method '%s' is not implemented" % method, "Call godot_get_capabilities for the method list.", {"method": method})
	var value: Variant = null
	if entry == "handle":
		value = module.call("handle", method, params)
	else:
		value = module.call(entry, params)
	return _as_response(value)


## Wraps a module return value into a success or failure envelope.
func _as_response(value: Variant) -> Dictionary:
	if McpErrors.is_error(value):
		return {"error": McpErrors.payload(value)}
	if value == null:
		return {"result": {}}
	return {"result": value}


## Builds a failure envelope.
func _fail(code: int, message: String, hint: String = "", context: Dictionary = {}) -> Dictionary:
	return {"error": McpErrors.payload(McpErrors.make(code, message, hint, context))}


# ---------------------------------------------------------------------------
# Module factory
# ---------------------------------------------------------------------------

## Loads and instantiates a module script, wiring the plugin instance in.
##
## The plugin calls this for the runtime bridge and the dock panel; keeping the
## reflection here means there is exactly one implementation of the contract.
##
## Returns:
##   The instance, or null when the script is missing or not instantiable.
func create_module(path: String) -> Object:
	if not ResourceLoader.exists(path):
		push_error("[MCP] module not found: " + path)
		return null
	var resource: Resource = load(path)
	if resource == null or not (resource is Script):
		push_error("[MCP] module is not a script: " + path)
		return null
	var script: Script = resource
	if not script.can_instantiate():
		push_error("[MCP] module cannot be instantiated: " + path)
		return null
	var instance: Object = null
	var init_args := _init_arg_count(script)
	if init_args >= 1:
		instance = script.new(host)
	elif init_args == 0:
		instance = script.new()
	else:
		# Reflection did not expose `_init`: try the host constructor, then plain.
		instance = script.new(host)
		if instance == null:
			instance = script.new()
	if instance != null and instance.has_method("setup"):
		instance.call("setup", host)
	return instance


## Loads the first existing script from `paths` and instantiates it.
##
## Returns:
##   The instance, or null when no candidate exists.
func create_first_module(paths: Array) -> Object:
	for path in paths:
		if ResourceLoader.exists(String(path)):
			return create_module(String(path))
	return null


## Number of declared `_init()` parameters, used to pick the right constructor.
##
## Returns:
##   The parameter count, or -1 when the script does not expose `_init`.
func _init_arg_count(script: Script) -> int:
	for info in script.get_script_method_list():
		var method_info: Dictionary = info
		if String(method_info.get("name", "")) == "_init":
			var args: Variant = method_info.get("args", [])
			if typeof(args) == TYPE_ARRAY:
				return (args as Array).size()
			return 0
	return -1


## Returns (and caches) the module instance for a group.
func _module(key: String) -> Object:
	if _modules.has(key):
		return _modules[key]
	var instance: Object = null
	if key == "runtime":
		instance = _existing_runtime()
	if instance == null and MODULE_PATHS.has(key):
		var candidates: Array = MODULE_PATHS[key]
		instance = create_first_module(candidates)
	if instance == null:
		push_warning("[MCP] no module available for group '%s'" % key)
	_modules[key] = instance
	return instance


## Reuses the runtime bridge instance owned by the plugin when available.
func _existing_runtime() -> Object:
	if host == null:
		return null
	var runtime: Variant = host.get("runtime")
	if runtime is Object:
		return runtime as Object
	return null


# ---------------------------------------------------------------------------
# System methods (PROTOCOL.md §3.1)
# ---------------------------------------------------------------------------

func _handle_system(method: String, params: Dictionary, client_id: String) -> Dictionary:
	match method:
		"ping":
			return {"result": _ping()}
		"auth":
			return {"result": {"ok": true, "client_id": client_id}}
		"get_capabilities":
			return {"result": _capabilities()}
		"get_editor_info":
			return {"result": _editor_info()}
		"get_config":
			return {"result": _config()}
		"set_config":
			return _set_config(params)
		"log.list":
			return _log_list(params)
		"log.clear":
			return _log_clear()
		_:
			return _fail(McpErrors.ERR_METHOD_NOT_FOUND, "unknown system method '%s'" % method, "Call godot_get_capabilities for the method list.")


func _ping() -> Dictionary:
	return {
		"pong": true,
		"protocol": PROTOCOL_VERSION,
		"project": _project_name(),
		"pid": OS.get_process_id(),
		"requires_auth": not _token().is_empty(),
	}


func _capabilities() -> Dictionary:
	return {
		"protocol": PROTOCOL_VERSION,
		"plugin_version": PLUGIN_VERSION,
		"godot_version": _godot_version(),
		"methods": METHODS.duplicate(),
		"groups": GROUPS.duplicate(),
	}


## Full editor snapshot required by PROTOCOL.md §3.1.
func _editor_info() -> Dictionary:
	var editor := _editor_interface()
	var root := _edited_root(editor)
	var scene_path := ""
	if root != null:
		scene_path = String(root.scene_file_path)
	return {
		"godot_version": _godot_version(),
		"system": OS.get_name(),
		"project_name": _project_name(),
		"project_path": McpHandshake.get_project_root(),
		"scene_path": scene_path,
		"scene_modified": _scene_modified(editor),
		"open_scenes": _open_scenes(editor, scene_path),
		"selection_count": _selection_count(editor),
		"is_playing": _is_playing(editor),
		"locale": TranslationServer.get_locale(),
	}


func _config() -> Dictionary:
	return {
		"port": int(_host_value("port", DEFAULT_PORT)),
		"runtime_port": int(_host_value("runtime_port", 0)),
		"token_masked": _mask_token(_token()),
		"auto_start": bool(_host_value("auto_start", true)),
		"has_runtime_bridge": _has_runtime_bridge(),
	}


## Applies configuration changes; a port change needs an editor restart.
func _set_config(params: Dictionary) -> Dictionary:
	var restart_required := false
	var touched := false
	if params.has("auto_start"):
		var auto_start_value: Variant = params["auto_start"]
		if typeof(auto_start_value) != TYPE_BOOL:
			return _fail(McpErrors.ERR_INVALID_PARAMS, "'auto_start' must be a boolean", "Example: {\"auto_start\": true}")
		ProjectSettings.set_setting(SETTING_AUTO_START, bool(auto_start_value))
		if host != null:
			host.set("auto_start", bool(auto_start_value))
		touched = true
	if params.has("port"):
		var port_value: Variant = params["port"]
		if typeof(port_value) != TYPE_INT and typeof(port_value) != TYPE_FLOAT:
			return _fail(McpErrors.ERR_INVALID_PARAMS, "'port' must be an integer", "Example: {\"port\": 8765}")
		var new_port := int(port_value)
		if new_port < 1024 or new_port > 65535:
			return _fail(McpErrors.ERR_INVALID_PARAMS, "port out of range: %d" % new_port, "Use a port between 1024 and 65535.", {"port": new_port})
		ProjectSettings.set_setting(SETTING_PORT, new_port)
		if host != null:
			host.set("port", new_port)
		restart_required = true
		touched = true
	if touched:
		ProjectSettings.save()
	return {"result": {
		"port": int(_host_value("port", DEFAULT_PORT)),
		"auto_start": bool(_host_value("auto_start", true)),
		"restart_required": restart_required,
	}}


func _log_list(params: Dictionary) -> Dictionary:
	var server: Variant = _server()
	if server == null:
		return {"result": {"entries": [], "next": 0}}
	var since := int(params.get("since", 0))
	var level := String(params.get("level", ""))
	var limit := int(params.get("limit", 200))
	if limit <= 0:
		limit = 200
	var data: Variant = server.call("get_logs", since, level, limit)
	if typeof(data) != TYPE_DICTIONARY:
		return _fail(McpErrors.ERR_INTERNAL, "log buffer returned an unexpected value", "")
	return {"result": data}


func _log_clear() -> Dictionary:
	var server: Variant = _server()
	if server == null:
		return {"result": {"cleared": 0}}
	var cleared: Variant = server.call("clear_logs")
	return {"result": {"cleared": int(cleared) if typeof(cleared) == TYPE_INT else 0}}


# ---------------------------------------------------------------------------
# Editor helpers (all version-guarded for Godot 4.3 - 4.7)
# ---------------------------------------------------------------------------

## EditorInterface singleton with a fallback to the owning plugin.
func _editor_interface() -> Object:
	var singleton: Object = Engine.get_singleton("EditorInterface")
	if singleton != null:
		return singleton
	if host != null and host.has_method("get_editor_interface"):
		var editor: Variant = host.call("get_editor_interface")
		if editor is Object:
			return editor as Object
	return null


func _edited_root(editor: Object) -> Node:
	if editor == null or not editor.has_method("get_edited_scene_root"):
		return null
	var root: Variant = editor.call("get_edited_scene_root")
	if root is Node:
		return root as Node
	return null


## Scene dirty flag, derived from EditorInterface.get_unsaved_scenes().
##
## Godot 4.7 has no `is_scene_modified()`; the shared helper keeps this answer
## identical to `scene.current.modified` and `scene.list_open[].modified`.
func _scene_modified(editor: Object) -> bool:
	if editor == null:
		return false
	return McpEditorState.current_scene_modified()


## Open scenes as [{path, current}] (PROTOCOL.md §3.1).
func _open_scenes(editor: Object, current_path: String) -> Array:
	var scenes: Array = []
	if editor == null or not editor.has_method("get_open_scenes"):
		return scenes
	var raw: Variant = editor.call("get_open_scenes")
	if typeof(raw) != TYPE_ARRAY and typeof(raw) != TYPE_PACKED_STRING_ARRAY:
		return scenes
	var count: int = int(raw.size())
	for index in range(count):
		var item: Variant = raw[index]
		var path := ""
		if typeof(item) == TYPE_STRING:
			path = String(item)
		elif item is Node:
			var node: Node = item
			path = String(node.scene_file_path)
			if path.is_empty():
				path = String(node.name)
		if path.is_empty():
			continue
		scenes.append({"path": path, "current": path == current_path})
	return scenes


func _selection_count(editor: Object) -> int:
	if editor == null or not editor.has_method("get_selection"):
		return 0
	var selection: Variant = editor.call("get_selection")
	if selection == null or not (selection is Object):
		return 0
	var selection_object: Object = selection
	if not selection_object.has_method("get_selected_nodes"):
		return 0
	var nodes: Variant = selection_object.call("get_selected_nodes")
	if typeof(nodes) == TYPE_ARRAY:
		return (nodes as Array).size()
	return 0


func _is_playing(editor: Object) -> bool:
	if editor == null:
		return false
	if editor.has_method("is_playing_scene"):
		return bool(editor.call("is_playing_scene"))
	return false


func _has_runtime_bridge() -> bool:
	if host != null and host.has_method("get_status"):
		var status: Variant = host.call("get_status")
		if typeof(status) == TYPE_DICTIONARY:
			return bool((status as Dictionary).get("has_runtime_bridge", false))
	return false


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------

func _token() -> String:
	if host != null and host.has_method("get_token"):
		return String(host.call("get_token"))
	return McpHandshake.get_token()


func _server() -> Variant:
	if host == null:
		return null
	return host.get("ws")


func _host_value(property: String, fallback: Variant) -> Variant:
	if host == null:
		return fallback
	var value: Variant = host.get(property)
	return fallback if value == null else value


func _project_name() -> String:
	return String(ProjectSettings.get_setting("application/config/name", ""))


func _godot_version() -> String:
	var info := Engine.get_version_info()
	return String(info.get("string", "unknown"))


## "abcd...wxyz" style mask so the token never leaks through get_config.
func _mask_token(value: String) -> String:
	if value.is_empty():
		return ""
	if value.length() <= 8:
		return "****"
	return "%s...%s" % [value.substr(0, 4), value.substr(value.length() - 4, 4)]


func _log_call(method: String, response: Dictionary, elapsed_ms: int, client_id: String) -> void:
	if host == null or not host.has_method("log_event"):
		return
	var ok := not response.has("error")
	var entry := {
		"level": "info" if ok else "error",
		"method": method,
		"ok": ok,
		"ms": elapsed_ms,
		"client_id": client_id,
		"error": "",
	}
	if not ok:
		var failure: Variant = response.get("error", {})
		if typeof(failure) == TYPE_DICTIONARY:
			entry["error"] = String((failure as Dictionary).get("message", ""))
	host.call("log_event", entry)
