@tool
extends RefCounted
## Script / resource / file / filesystem layer of the MCP bridge.
## File-level writes are undoable through _FileAction (PROTOCOL.md §3 "W" rule).

const McpErrors = preload("res://addons/godot_mcp/core/errors.gd")
const McpValue = preload("res://addons/godot_mcp/core/value_codec.gd")

## Default globs for fs.search / script.search (PROTOCOL.md §3.7).
const DEFAULT_GLOB := ["*.gd", "*.tscn", "*.tres", "*.res", "*.json", "*.cfg", "*.txt", "*.md"]
const MAX_SEARCH_BYTES := 2 * 1024 * 1024
const MAX_READ_BYTES := 1024 * 1024
const SKIP_DIRS := [".godot", ".import", "__pycache__", ".git"]
const TYPE_MAP := {".tscn": "PackedScene", ".scn": "PackedScene", ".escn": "PackedScene",
	".tres": "Resource", ".res": "Resource", ".gd": "GDScript", ".gdshader": "Shader",
	".png": "Texture2D", ".svg": "Texture2D", ".jpg": "Texture2D", ".jpeg": "Texture2D",
	".webp": "Texture2D", ".bmp": "Texture2D", ".wav": "AudioStream", ".ogg": "AudioStream",
	".mp3": "AudioStream", ".json": "JSON", ".cfg": "ConfigFile"}
const RE_CLASS := "^\\s*class_name\\s+([A-Za-z_][A-Za-z0-9_]*)"
const RE_EXTENDS := "^\\s*extends\\s+([^\\s#]+)"


## Undo payload for one file write/delete; keeps the previous bytes intact.
class _FileAction extends RefCounted:
	var path := ""
	var before := PackedByteArray()
	var after := PackedByteArray()
	var had_before := false
	var write_on_do := true

	func do_apply() -> void:
		if write_on_do: _write(after)
		else: _delete()

	func undo_apply() -> void:
		if had_before: _write(before)
		else: _delete()

	func _write(bytes: PackedByteArray) -> void:
		var file := FileAccess.open(path, FileAccess.WRITE)
		if file == null: return
		file.store_buffer(bytes)
		file.close()

	func _delete() -> void:
		if FileAccess.file_exists(path):
			DirAccess.remove_absolute(ProjectSettings.globalize_path(path))


var _plugin: Object = null
var _re_class: RegEx = null
var _re_extends: RegEx = null


func _init(plugin: Object) -> void:
	_plugin = plugin
	_re_class = RegEx.create_from_string(RE_CLASS)
	_re_extends = RegEx.create_from_string(RE_EXTENDS)


## Routes one RPC method; returns the result payload or an McpErrors failure.
func handle(action: String, params: Dictionary) -> Dictionary:
	match action:
		"script.read": return _script_read(params)
		"script.write": return _script_write(params)
		"script.create": return _script_create(params)
		"script.delete": return _script_delete(params)
		"script.attach": return _script_attach(params)
		"script.detach": return _script_detach(params)
		"script.check": return _script_check(params)
		"script.list": return _script_list(params)
		"script.search": return _script_search(params)
		"res.list": return _res_list(params)
		"res.info": return _res_info(params)
		"res.load": return _res_load(params)
		"res.create": return _res_create(params)
		"res.duplicate": return _res_duplicate(params)
		"res.delete": return _res_delete(params)
		"res.dependencies": return _res_dependencies(params)
		"res.reimport": return _res_reimport(params)
		"file.read": return _file_read(params)
		"file.write": return _file_write(params)
		"fs.search": return _fs_search(params)
		"fs.list_dir": return _fs_list_dir(params)
	return _err(-32601, "method '%s' is not handled by resource_ops" % action, "Call get_capabilities to list methods.", {"method": action})

# ------------------------------------------------------------- infrastructure
func _err(code: int, message: String, hint := "", context: Dictionary = {}) -> Dictionary:
	return McpErrors.fail(code, message, hint, context)

func _s(p: Dictionary, key: String, def := "") -> String:
	return String(p.get(key, def))

func _i(p: Dictionary, key: String, def := 0) -> int:
	return int(p.get(key, def))

func _b(p: Dictionary, key: String, def := false) -> bool:
	return bool(p.get(key, def))

func _missing(param_name: String) -> Dictionary:
	return _err(-32602, "missing required param '%s'" % param_name,
		"Send the '%s' field, e.g. {\"%s\": \"res://main.gd\"}." % [param_name, param_name], {"param": param_name})

func _undo_mgr():
	if _plugin == null: return null
	if _plugin.has_method("get_undo"): return _plugin.get_undo()
	if _plugin.has_method("get_undo_redo"): return _plugin.get_undo_redo()
	return null

## Validates a project path. Returns {"ok": true, "path": ...} or an error dict.
func _safe_path(raw: String, must_exist := true) -> Dictionary:
	var path := raw.strip_edges().replace("\\", "/")
	if path.is_empty(): return _missing("path")
	if path.contains(".."):
		return _err(1005, "path must not contain '..': '%s'" % raw, "Pass a normalized res:// path.", {"path": raw})
	if path.begins_with("user://"):
		if must_exist and not FileAccess.file_exists(path):
			return _err(1006, "file not found: '%s'" % path, "Call godot_list_directory to list the directory.", {"path": path})
		return {"ok": true, "path": path}
	var res_path := path
	if path == "res://" or path.begins_with("res://"):
		res_path = path
	elif path.is_absolute_path():
		var root := ProjectSettings.globalize_path("res://").simplify_path().trim_suffix("/")
		var absolute := path.simplify_path()
		if absolute == root: res_path = "res://"
		elif absolute.begins_with(root + "/"): res_path = "res://" + absolute.substr(root.length() + 1)
		else: return _err(1005, "path is outside the project: '%s'" % raw, "Use a res:// or user:// path inside the project.", {"path": raw})
	else:
		return _err(1005, "'%s' is not a res://, user:// or in-project absolute path" % raw,
			"Prefix project paths with res:// and exports with user://.", {"path": raw})
	res_path = res_path.simplify_path()
	var global := ProjectSettings.globalize_path(res_path).simplify_path()
	var project_root := ProjectSettings.globalize_path("res://").simplify_path().trim_suffix("/")
	if global != project_root and not global.begins_with(project_root + "/"):
		return _err(1005, "path escapes the project: '%s'" % res_path, "Stay inside res:// or user://.", {"path": res_path})
	if must_exist and not _path_exists(res_path):
		return _err(1006, "file not found: '%s'" % res_path, "Call godot_list_directory to list the directory contents.", {"path": res_path})
	return {"ok": true, "path": res_path}

func _path_exists(path: String) -> bool:
	return FileAccess.file_exists(path) or DirAccess.dir_exists_absolute(path)

func _res_has_property(res: Object, prop: String) -> bool:
	for info in res.get_property_list():
		if String(info.get("name", "")) == prop: return true
	return false

## Builds the 1004 envelope for a failed decode without String()-ing the error.
func _codec_error(prop: String, current: Variant, dec: Dictionary) -> Dictionary:
	var expected := type_string(typeof(current))
	var detail := "the value does not match the property type"
	var payload := McpErrors.payload(dec.get("error", null))
	if not payload.is_empty():
		var message := String(payload.get("message", ""))
		if not message.is_empty(): detail = message
	elif typeof(dec.get("message", null)) == TYPE_STRING and not String(dec.get("message")).is_empty():
		detail = String(dec.get("message"))
	return _err(1004, "cannot convert value for '%s' to %s: %s" % [prop, expected, detail],
		"Send a %s value such as {\"__gd\":\"%s\", ...}." % [expected, expected], {"property": prop})

## Resolves a client node path exactly like `editor_ops._resolve()`:
## ""/"." is the edited scene root, "/root/..." is accepted (the prefix and the
## scene-root segment are stripped), everything else is scene relative.
func _node(p: Dictionary, key := "path") -> Node:
	var raw := _s(p, key).strip_edges()
	var root := EditorInterface.get_edited_scene_root()
	if raw == "" or raw == ".": return root
	if raw.contains(".."): return null
	if raw == "/root" or raw == "/root/": return root
	if raw.begins_with("/root/"):
		raw = raw.trim_prefix("/root/")
	elif raw.begins_with("/"):
		return null
	if raw.begins_with("./"): raw = raw.substr(2)
	if raw == "" or raw == ".": return root
	if root == null: return null
	var head := String(raw.get_slice("/", 0))
	if head == String(root.name):
		raw = raw.substr(head.length()).trim_prefix("/")
		if raw.is_empty(): return root
	return root.get_node_or_null(raw)

func _entries(path: String) -> PackedByteArray:
	return FileAccess.get_file_as_bytes(path) if FileAccess.file_exists(path) else PackedByteArray()

## Registers one undoable file mutation and executes it through the undo manager.
##
## Args:
##   path: res:// or user:// target.
##   after: Bytes written on do (ignored when write_on_do is false).
##   write_on_do: false turns the do step into a delete (used by *_delete).
##   before: Bytes restored when the action is undone.
##   action_name: History label, "MCP: <verb> <target>".
func _apply_file(path: String, after: PackedByteArray, write_on_do: bool, before: PackedByteArray,
		action_name: String) -> void:
	var action := _FileAction.new()
	action.path = path
	action.before = before
	action.after = after
	action.had_before = write_on_do and (not before.is_empty() or FileAccess.file_exists(path))
	action.write_on_do = write_on_do
	if not write_on_do: action.had_before = true
	var mgr = _undo_mgr()
	if mgr == null:
		action.do_apply()
		return
	mgr.create_action(action_name)
	mgr.add_do_method(action, "do_apply")
	mgr.add_undo_method(action, "undo_apply")
	mgr.add_undo_reference(action)
	mgr.commit_action()

# -------------------------------------------------------------------- script
func _script_read(p: Dictionary) -> Dictionary:
	var safe := _safe_path(_s(p, "path"))
	if not safe.get("ok", false): return safe
	var path := String(safe["path"])
	var source := FileAccess.get_file_as_string(path)
	return {"path": path, "source": source, "lines": source.split("\n").size() if not source.is_empty() else 0}

func _script_write(p: Dictionary) -> Dictionary:
	var safe := _safe_path(_s(p, "path"), false)
	if not safe.get("ok", false): return safe
	var path := String(safe["path"])
	if not path.ends_with(".gd"):
		return _err(1004, "'%s' is not a .gd script" % path, "Use a .gd file name such as res://player.gd.", {"path": path})
	var source := _s(p, "source")
	if not p.has("source"):
		return _err(-32602, "missing required param 'source'", "Send the full script text.", {"path": path})
	if source.strip_edges().is_empty():
		return _err(-32602, "param 'source' must not be empty", "Send the full script text.", {"path": path})
	_ensure_parent(path)
	var converted := _match_newline_style(path, source)
	var bytes := converted.to_utf8_buffer()
	_apply_file(path, bytes, true, _entries(path), "MCP: script.write %s" % path)
	return {"path": path, "bytes": bytes.size()}

## Preserves the existing newline style and drops any BOM (PROTOCOL.md §3.6).
func _match_newline_style(path: String, source: String) -> String:
	var clean := source.trim_prefix("\ufeff")
	if not FileAccess.file_exists(path): return clean
	var previous := FileAccess.get_file_as_string(path)
	if previous.contains("\r\n"):
		return clean.replace("\r\n", "\n").replace("\n", "\r\n")
	return clean.replace("\r\n", "\n")

func _script_create(p: Dictionary) -> Dictionary:
	var safe := _safe_path(_s(p, "path"), false)
	if not safe.get("ok", false): return safe
	var path := String(safe["path"])
	if not path.ends_with(".gd"):
		return _err(1004, "'%s' is not a .gd script" % path, "Use a .gd file name such as res://player.gd.", {"path": path})
	if FileAccess.file_exists(path):
		return _err(1013, "script already exists: '%s'" % path, "Call godot_write_script to overwrite it.", {"path": path})
	var source := _s(p, "source")
	if source.is_empty(): source = "extends %s\n" % _s(p, "extends", "Node")
	_ensure_parent(path)
	var bytes := _match_newline_style(path, source).to_utf8_buffer()
	_apply_file(path, bytes, true, PackedByteArray(), "MCP: script.create %s" % path)
	return {"path": path, "bytes": bytes.size()}

func _script_delete(p: Dictionary) -> Dictionary:
	var safe := _safe_path(_s(p, "path"))
	if not safe.get("ok", false): return safe
	var path := String(safe["path"])
	if not path.ends_with(".gd"):
		return _err(1004, "'%s' is not a .gd script" % path, "Use godot_delete_resource for non-script resources.", {"path": path})
	var before := _entries(path)
	_apply_file(path, PackedByteArray(), false, before, "MCP: script.delete %s" % path)
	var attached := _script_in_use(path)
	var result := {"deleted": true, "path": path}
	if attached: result["hint"] = "The script was still attached to a node in the edited scene; press Ctrl+Z to restore it."
	return result

## Best effort check: is the script attached to any node of the edited scene?
func _script_in_use(path: String) -> bool:
	var root := EditorInterface.get_edited_scene_root()
	if root == null: return false
	var pending: Array = [root]
	var guard := 0
	while not pending.is_empty() and guard < 5000:
		var node: Node = pending.pop_back()
		var script = node.get_script()
		if script is Script and String(script.resource_path) == path: return true
		for child in node.get_children(): pending.append(child)
		guard += 1
	return false

func _script_attach(p: Dictionary) -> Dictionary:
	var node := _node(p, "node_path")
	if node == null:
		return _err(1002, "node not found: '%s'" % _s(p, "node_path"), "Call godot_get_scene_tree to list valid paths.", {"path": _s(p, "node_path")})
	var safe := _safe_path(_s(p, "script_path"))
	if not safe.get("ok", false): return safe
	var path := String(safe["path"])
	var res = load(path)
	if not (res is GDScript):
		return _err(1004, "'%s' is not a GDScript" % path, "Pass a .gd file path.", {"path": path})
	var previous = node.get_script()
	var mgr = _undo_mgr()
	if mgr == null: return _err(-32603, "the editor undo manager is not available", "Reload the MCP Bridge plugin.")
	mgr.create_action("MCP: script.attach %s" % path)
	mgr.add_do_property(node, "script", res)
	mgr.add_do_method(node, "set_owner", EditorInterface.get_edited_scene_root())
	mgr.add_undo_property(node, "script", previous)
	mgr.commit_action()
	return {"attached": true, "path": path}

func _script_detach(p: Dictionary) -> Dictionary:
	var node := _node(p, "node_path")
	if node == null:
		return _err(1002, "node not found: '%s'" % _s(p, "node_path"), "Call godot_get_scene_tree to list valid paths.", {"path": _s(p, "node_path")})
	var previous = node.get_script()
	if previous == null:
		return {"detached": false, "hint": "The node already has no script attached."}
	var mgr = _undo_mgr()
	if mgr == null: return _err(-32603, "the editor undo manager is not available", "Reload the MCP Bridge plugin.")
	mgr.create_action("MCP: script.detach %s" % _s(p, "node_path"))
	mgr.add_do_property(node, "script", null)
	mgr.add_undo_property(node, "script", previous)
	mgr.commit_action()
	return {"detached": true}

func _script_check(p: Dictionary) -> Dictionary:
	var source := _s(p, "source")
	if source.is_empty():
		var safe := _safe_path(_s(p, "path"))
		if not safe.get("ok", false): return safe
		source = FileAccess.get_file_as_string(String(safe["path"]))
	if source.is_empty():
		return _err(-32602, "nothing to check: pass 'source' or a readable 'path'", "Send the script text or a valid res:// path.")
	var probe := GDScript.new()
	probe.source_code = source
	var err := probe.reload()
	if err == OK: return {"ok": true, "errors": []}
	return {"ok": false, "errors": [{"line": 0, "message": error_string(err)}]}

func _script_list(p: Dictionary) -> Dictionary:
	var safe := _safe_path(_s(p, "dir", "res://"), true)
	if not safe.get("ok", false): return safe
	var files: Array = []
	_scan(String(safe["path"]), ["*.gd"], _b(p, "recursive", true), files, _i(p, "limit", 500))
	var scripts: Array = []
	for path in files:
		var info := _script_header(String(path))
		scripts.append({"path": path, "class_name": info[0], "extends": info[1]})
	return {"scripts": scripts, "count": scripts.size()}

func _script_header(path: String) -> Array:
	var declared_class := ""
	var base := ""
	var file := FileAccess.open(path, FileAccess.READ)
	if file == null: return ["", ""]
	var line_no := 0
	while not file.eof_reached() and line_no < 200:
		var line := file.get_line()
		line_no += 1
		if declared_class.is_empty():
			var hit := _re_class.search(line)
			if hit != null: declared_class = hit.get_string(1)
		if base.is_empty():
			var parent := _re_extends.search(line)
			if parent != null: base = parent.get_string(1).strip_edges()
		if not declared_class.is_empty() and not base.is_empty(): break
	file.close()
	return [declared_class, base]

func _script_search(p: Dictionary) -> Dictionary:
	return _search(p)

# ---------------------------------------------------------------- resource
func _res_list(p: Dictionary) -> Dictionary:
	var safe := _safe_path(_s(p, "dir", "res://"), true)
	if not safe.get("ok", false): return safe
	var type_filter := _s(p, "type")
	var extensions: Array = []
	for ext in TYPE_MAP.keys():
		if type_filter.is_empty() or String(TYPE_MAP[ext]) == type_filter: extensions.append("*" + String(ext))
	if extensions.is_empty():
		return _err(-32602, "unknown resource type '%s'" % type_filter, "Use PackedScene, Resource, GDScript, Texture2D or AudioStream.", {"type": type_filter})
	var files: Array = []
	_scan(String(safe["path"]), extensions, _b(p, "recursive", true), files, maxi(1, _i(p, "limit", 500)))
	var entries: Array = []
	for path in files:
		var file_path := String(path)
		entries.append({"path": file_path, "type": _type_for(file_path), "size": _size_of(file_path)})
	return {"entries": entries, "count": entries.size()}

func _type_for(path: String) -> String:
	return String(TYPE_MAP.get("." + path.get_extension().to_lower(), "Resource"))

func _size_of(path: String) -> int:
	if not FileAccess.file_exists(path): return -1
	var file := FileAccess.open(path, FileAccess.READ)
	if file == null: return -1
	var size := int(file.get_length())
	file.close()
	return size if size <= MAX_SEARCH_BYTES else -1

func _res_info(p: Dictionary) -> Dictionary:
	var safe := _safe_path(_s(p, "path"))
	if not safe.get("ok", false): return safe
	var path := String(safe["path"])
	if not ResourceLoader.exists(path):
		return _err(1006, "resource not found: '%s'" % path, "Call godot_list_resources to find resources.", {"path": path})
	var res = ResourceLoader.load(path)
	# A resource that was never imported has no UID; report "" instead of the
	# placeholder "uid://<invalid>" (PROTOCOL.md §3.7).
	var uid := ""
	var uid_id: int = ResourceLoader.get_resource_uid(path)
	if uid_id != ResourceUID.INVALID_ID and ResourceUID.has_method("id_to_text"):
		uid = String(ResourceUID.call("id_to_text", uid_id))
		if uid.contains("<invalid>"):
			uid = ""
	return {"path": path, "type": res.get_class() if res != null else "", "resource_path": path,
		"uid": uid, "imported": FileAccess.file_exists(path + ".import")}

func _res_load(p: Dictionary) -> Dictionary:
	var safe := _safe_path(_s(p, "path"))
	if not safe.get("ok", false): return safe
	var path := String(safe["path"])
	var res = ResourceLoader.load(path)
	if res == null:
		return _err(1006, "failed to load resource: '%s'" % path, "Check the Godot output panel for import errors.", {"path": path})
	var properties := {}
	var count := 0
	for info in res.get_property_list():
		if count >= 200: break
		if not (int(info.get("usage", 0)) & PROPERTY_USAGE_STORAGE): continue
		var pname := String(info.get("name", ""))
		if pname.is_empty() or pname.begins_with("_"): continue
		properties[pname] = McpValue.encode(res.get(pname))
		count += 1
	return {"type": res.get_class(), "properties": properties}

func _res_create(p: Dictionary) -> Dictionary:
	var safe := _safe_path(_s(p, "path"), false)
	if not safe.get("ok", false): return safe
	var path := String(safe["path"])
	if _path_exists(path):
		return _err(1013, "resource already exists: '%s'" % path, "Call godot_delete_resource first, or pick another path.", {"path": path})
	var type_name := _s(p, "type")
	if type_name.is_empty(): return _missing("type")
	if not ClassDB.class_exists(type_name) or not ClassDB.can_instantiate(type_name) or (type_name != "Resource" and not ClassDB.is_parent_class(type_name, "Resource")):
		return _err(1004, "'%s' is not an instantiable Resource" % type_name, "Use a resource class such as Resource, Curve or Gradient.", {"type": type_name})
	var res: Resource = ClassDB.instantiate(type_name)
	if res == null: return _err(1004, "could not instantiate '%s'" % type_name, "Pick a concrete Resource class.", {"type": type_name})
	var properties: Variant = p.get("properties", {})
	if properties is Dictionary:
		for key in (properties as Dictionary).keys():
			var prop := String(key)
			if not _res_has_property(res, prop):
				return _err(1003, "property '%s' does not exist on %s" % [prop, type_name],
					"Call godot_get_class_info with name '%s' to list its properties." % type_name, {"property": prop})
			var current: Variant = res.get(prop)
			var dec: Dictionary = McpValue.decode_checked((properties as Dictionary)[key], typeof(current))
			if not bool(dec.get("ok", false)):
				return _codec_error(prop, current, dec)
			res.set(prop, dec.get("value"))
	_ensure_parent(path)
	var err := ResourceSaver.save(res, path)
	if err != OK:
		return _err(-32603, "saving '%s' failed: %s" % [path, error_string(err)], "Make sure the parent directory exists.", {"path": path})
	return {"path": path, "type": res.get_class()}

func _res_duplicate(p: Dictionary) -> Dictionary:
	var src_safe := _safe_path(_s(p, "src"))
	if not src_safe.get("ok", false): return src_safe
	var dst_safe := _safe_path(_s(p, "dst"), false)
	if not dst_safe.get("ok", false): return dst_safe
	var src := String(src_safe["path"])
	var dst := String(dst_safe["path"])
	if _path_exists(dst):
		return _err(1013, "destination already exists: '%s'" % dst, "Pick a new path or delete the existing file.", {"path": dst})
	var res = ResourceLoader.load(src)
	if res == null:
		return _err(1006, "could not load '%s'" % src, "Call godot_get_resource_info to inspect the source resource.", {"path": src})
	_ensure_parent(dst)
	var copy = res.duplicate(true)
	var err := ResourceSaver.save(copy, dst)
	if err != OK:
		return _err(-32603, "saving '%s' failed: %s" % [dst, error_string(err)], "Make sure the parent directory exists.", {"path": dst})
	return {"path": dst}

func _res_delete(p: Dictionary) -> Dictionary:
	var safe := _safe_path(_s(p, "path"))
	if not safe.get("ok", false): return safe
	var path := String(safe["path"])
	var before := _entries(path)
	_apply_file(path, PackedByteArray(), false, before, "MCP: res.delete %s" % path)
	return {"deleted": true, "path": path}

func _res_dependencies(p: Dictionary) -> Dictionary:
	var safe := _safe_path(_s(p, "path"))
	if not safe.get("ok", false): return safe
	var path := String(safe["path"])
	var out: Array = []
	for dependency in ResourceLoader.get_dependencies(path):
		var dependency_path := String(dependency)
		var dependency_type := ""
		if ResourceLoader.exists(dependency_path):
			var dependency_resource: Resource = ResourceLoader.load(dependency_path)
			if dependency_resource != null:
				dependency_type = dependency_resource.get_class()
		out.append({"path": dependency_path, "type": dependency_type})
	return {"dependencies": out}

func _res_reimport(_p: Dictionary) -> Dictionary:
	if not EditorInterface.has_method("get_resource_filesystem"):
		return _err(1014, "the editor filesystem API is not available", "Upgrade to Godot 4.3 or newer.")
	var filesystem = EditorInterface.get_resource_filesystem()
	if filesystem == null or not filesystem.has_method("scan"):
		return _err(1014, "the editor filesystem API is not available", "Upgrade to Godot 4.3 or newer.")
	filesystem.scan()
	return {"scanned": true, "files": 0}

# ---------------------------------------------------------------------- file
func _file_read(p: Dictionary) -> Dictionary:
	var safe := _safe_path(_s(p, "path"))
	if not safe.get("ok", false): return safe
	var path := String(safe["path"])
	var limit := maxi(1, _i(p, "max_bytes", MAX_READ_BYTES))
	var bytes := _entries(path)
	var truncated := bytes.size() > limit
	var payload := bytes.slice(0, limit) if truncated else bytes
	var result := {"path": path, "size": bytes.size(), "encoding": "utf-8"}
	if payload.find(0) >= 0:
		result["encoding"] = "base64"
		result["content"] = Marshalls.raw_to_base64(payload)
	else:
		result["content"] = payload.get_string_from_utf8()
	if truncated: result["truncated"] = true
	return result

func _file_write(p: Dictionary) -> Dictionary:
	var safe := _safe_path(_s(p, "path"), false)
	if not safe.get("ok", false): return safe
	var path := String(safe["path"])
	var content := _s(p, "content")
	var bytes := content.to_utf8_buffer()
	if _s(p, "encoding", "utf-8") == "base64":
		bytes = Marshalls.base64_to_raw(content)
	_ensure_parent(path)
	_apply_file(path, bytes, true, _entries(path), "MCP: file.write %s" % path)
	return {"path": path, "bytes": bytes.size()}

func _fs_search(p: Dictionary) -> Dictionary:
	return _search(p)

func _fs_list_dir(p: Dictionary) -> Dictionary:
	var safe := _safe_path(_s(p, "path", "res://"), true)
	if not safe.get("ok", false): return safe
	var dir_path := String(safe["path"])
	var dir := DirAccess.open(dir_path)
	if dir == null:
		return _err(1006, "cannot open directory '%s'" % dir_path, "Call godot_list_directory with path 'res://'.", {"path": dir_path})
	var show_hidden := _b(p, "show_hidden")
	var entries: Array = []
	dir.list_dir_begin()
	var name := dir.get_next()
	while name != "":
		if not show_hidden and name.begins_with("."):
			name = dir.get_next()
			continue
		var full := dir_path.path_join(name)
		var is_dir := dir.current_is_dir()
		entries.append({"name": name, "path": full, "dir": is_dir, "size": -1 if is_dir else _size_of(full)})
		name = dir.get_next()
	dir.list_dir_end()
	return {"entries": entries, "count": entries.size()}

# ------------------------------------------------------------------ helpers
func _search(p: Dictionary) -> Dictionary:
	var safe := _safe_path(_s(p, "dir", "res://"), true)
	if not safe.get("ok", false): return safe
	var query := _s(p, "query")
	if query.is_empty(): return _missing("query")
	var patterns: Array = []
	for part in _s(p, "glob").split(";", false):
		if not part.strip_edges().is_empty(): patterns.append(part.strip_edges())
	if patterns.is_empty(): patterns = DEFAULT_GLOB
	var matches: Array = []
	_walk(String(safe["path"]), patterns, query, maxi(1, _i(p, "limit", 100)), matches)
	return {"matches": matches, "count": matches.size()}

func _walk(dir_path: String, patterns: Array, query: String, limit: int, out: Array) -> void:
	if out.size() >= limit: return
	var dir := DirAccess.open(dir_path)
	if dir == null: return
	dir.list_dir_begin()
	var name := dir.get_next()
	while name != "":
		if out.size() >= limit: break
		if name.begins_with(".") or SKIP_DIRS.has(name):
			name = dir.get_next()
			continue
		var full := dir_path.path_join(name)
		if dir.current_is_dir(): _walk(full, patterns, query, limit, out)
		elif _matches_glob(name, patterns): _search_file(full, query, limit, out)
		name = dir.get_next()
	dir.list_dir_end()

func _matches_glob(name: String, patterns: Array) -> bool:
	for pattern in patterns:
		if name.match(String(pattern)): return true
	return false

func _search_file(path: String, query: String, limit: int, out: Array) -> void:
	if out.size() >= limit or not FileAccess.file_exists(path): return
	var file := FileAccess.open(path, FileAccess.READ)
	if file == null: return
	if int(file.get_length()) > MAX_SEARCH_BYTES:
		file.close()
		return
	var head := file.get_buffer(512)
	if head.find(0) >= 0:
		file.close()
		return
	file.seek(0)
	var line_no := 0
	while not file.eof_reached() and out.size() < limit:
		var line := file.get_line()
		line_no += 1
		if line.findn(query) >= 0:
			out.append({"path": path, "line": line_no, "text": line.strip_edges().substr(0, 400)})
	file.close()

## Collects files below dir matching the glob patterns into out (skips dot dirs).
func _scan(dir_path: String, patterns: Array, recursive: bool, out: Array, limit: int) -> void:
	if out.size() >= limit: return
	var dir := DirAccess.open(dir_path)
	if dir == null: return
	dir.list_dir_begin()
	var name := dir.get_next()
	while name != "":
		if out.size() >= limit: break
		if name.begins_with(".") or SKIP_DIRS.has(name):
			name = dir.get_next()
			continue
		var full := dir_path.path_join(name)
		if dir.current_is_dir():
			if recursive: _scan(full, patterns, true, out, limit)
		elif _matches_glob(name, patterns):
			out.append(full)
		name = dir.get_next()
	dir.list_dir_end()

func _ensure_parent(path: String) -> void:
	var parent := path.get_base_dir()
	if parent.is_empty() or parent == "res://" or parent == "user://": return
	if DirAccess.dir_exists_absolute(parent): return
	var absolute := ProjectSettings.globalize_path(parent)
	if not absolute.is_empty(): DirAccess.make_dir_recursive_absolute(absolute)
