@tool
extends RefCounted
## Editor layer: scene / node / prop / signal / editor.
## Every write runs inside exactly one "MCP: <verb> <target>" undo action.

const McpErrors = preload("res://addons/godot_mcp/core/errors.gd")
const McpValue = preload("res://addons/godot_mcp/core/value_codec.gd")
const McpEditorState = preload("res://addons/godot_mcp/core/editor_state.gd")
const HINT_TREE := "Call godot_get_scene_tree to list valid node paths."
const HINT_SCENE := "Call godot_create_scene or godot_open_scene to load a scene first."
const HINT_UNDO := "Reload the MCP Bridge plugin; the editor undo manager is required."
## Upper bound of `editor.undo_history` entries (newest first).
const MAX_UNDO_HISTORY := 50
const HINT_NAMES := {PROPERTY_HINT_NONE: "none", PROPERTY_HINT_RANGE: "range", PROPERTY_HINT_ENUM: "enum",
	PROPERTY_HINT_FLAGS: "flags", PROPERTY_HINT_FILE: "file", PROPERTY_HINT_DIR: "dir",
	PROPERTY_HINT_MULTILINE_TEXT: "multiline_text", PROPERTY_HINT_COLOR_NO_ALPHA: "color_no_alpha",
	PROPERTY_HINT_RESOURCE_TYPE: "resource_type", PROPERTY_HINT_TYPE_STRING: "type_string",
	PROPERTY_HINT_OBJECT_ID: "object_id", PROPERTY_HINT_ARRAY_TYPE: "array_type"}

var _plugin: Object = null

func _init(plugin: Object) -> void:
	_plugin = plugin

## Routes one RPC method; returns the result payload or an McpErrors failure dict.
func handle(action: String, params: Dictionary) -> Dictionary:
	match action:
		"scene.get_tree": return _scene_get_tree(params)
		"scene.current": return _scene_current(params)
		"scene.list_open": return _scene_list_open(params)
		"scene.open": return _scene_open(params)
		"scene.save": return _scene_save(params)
		"scene.save_as": return _scene_save_as(params)
		"scene.create": return _scene_create(params)
		"node.get": return _node_get(params)
		"node.add": return _node_add(params)
		"node.remove": return _node_remove(params)
		"node.rename": return _node_rename(params)
		"node.move": return _node_move(params)
		"node.duplicate": return _node_duplicate(params)
		"node.find": return _node_find(params)
		"node.call": return _node_call(params)
		"prop.list": return _prop_list(params)
		"prop.get": return _prop_get(params)
		"prop.get_all": return _prop_get_all(params)
		"prop.set": return _prop_set(params)
		"prop.set_many": return _prop_set_many(params)
		"prop.reset": return _prop_reset(params)
		"signal.list": return _signal_list(params)
		"signal.connect": return _signal_connect(params)
		"signal.disconnect": return _signal_disconnect(params)
		"editor.get_selection": return _editor_get_selection(params)
		"editor.set_selection": return _editor_set_selection(params)
		"editor.undo": return _editor_undo(params)
		"editor.redo": return _editor_redo(params)
		"editor.undo_history": return _editor_undo_history(params)
		"editor.inspect": return _editor_inspect(params)
		"editor.open_script": return _editor_open_script(params)
		"editor.project_settings": return _editor_project_settings(params)
		"editor.set_project_setting": return _editor_set_project_setting(params)
		"editor.autoloads": return _editor_autoloads(params)
	return _err(-32601, "method '%s' is not handled by editor_ops" % action, "Call get_capabilities to list methods.", {"method": action})

# ------------------------------------------------------------- infrastructure
func _err(code: int, message: String, hint := "", context: Dictionary = {}) -> Dictionary:
	return McpErrors.fail(code, message, hint, context)

func _s(p: Dictionary, key: String, def := "") -> String:
	return String(p.get(key, def))

func _i(p: Dictionary, key: String, def := 0) -> int:
	return int(p.get(key, def))

func _b(p: Dictionary, key: String, def := false) -> bool:
	return bool(p.get(key, def))

## Typed param readers; keep bad client payloads from raising inside GDScript.
func _dict(p: Dictionary, key: String) -> Dictionary:
	var value: Variant = p.get(key, {})
	return value if value is Dictionary else {}

func _arr(p: Dictionary, key: String) -> Array:
	var value: Variant = p.get(key, [])
	return value if value is Array else []

func _edited_root() -> Node:
	return EditorInterface.get_edited_scene_root()

## Resolves a client node path to a Node.
##
## Accepted forms (PROTOCOL.md §3.3):
##   ""  /  "."                    -> edited scene root
##   "Player"                      -> relative to the edited scene root
##   "/root/Player"                -> absolute editor path (root prefix dropped)
##   "/root/<scene root name>/..." -> same as the path below the scene root
##
## The editor's edited scene root is *not* a child of the main SceneTree root,
## so `/root/...` can never be looked up through `get_tree().root` - the prefix
## is stripped first and the rest is resolved against the edited scene root,
## which makes "/root/Player" exactly equivalent to "Player".
func _resolve(path: String) -> Node:
	var root := _edited_root()
	var p := path.strip_edges()
	if p == "" or p == ".": return root
	if p.contains(".."): return null
	var from_root := false
	if p == "/root" or p == "/root/":
		return root
	if p.begins_with("/root/"):
		p = p.trim_prefix("/root/")
		from_root = true
	elif p.begins_with("/"):
		return null
	if p.begins_with("./"): p = p.substr(2)
	if p == "" or p == ".": return root
	if root == null: return null
	var head := String(p.get_slice("/", 0))
	if head == String(root.name):
		p = p.substr(head.length()).trim_prefix("/")
		if p.is_empty(): return root
	elif from_root and head.begins_with("@"):
		return null
	return root.get_node_or_null(p)

func _path_of(obj: Object) -> String:
	var node := obj as Node
	if node == null or not is_instance_valid(node): return ""
	var root := _edited_root()
	if root == null: return ""
	return "." if node == root else String(root.get_path_to(node))

func _script_path(node: Node) -> String:
	var scr = node.get_script() if node != null else null
	return String(scr.resource_path) if scr is Script and not String(scr.resource_path).is_empty() else ""

func _no_scene() -> Dictionary:
	return _err(1001, "no scene is currently open in the editor", HINT_SCENE)

func _not_found(path: String) -> Dictionary:
	return _err(1002, "node not found: '%s'" % path, HINT_TREE, {"path": path})

func _no_undo() -> Dictionary:
	return _err(-32603, "the editor undo manager is not available", HINT_UNDO)

func _missing(param_name: String) -> Dictionary:
	return _err(-32602, "missing required param '%s'" % param_name,
		"Send the '%s' field together with 'path', e.g. godot_get_property." % param_name, {"param": param_name})

func _undo_mgr() -> EditorUndoRedoManager:
	if _plugin == null: return null
	if _plugin.has_method("get_undo"):
		var manager: Variant = _plugin.get_undo()
		return manager as EditorUndoRedoManager
	if _plugin.has_method("get_undo_redo"):
		var legacy_manager: Variant = _plugin.get_undo_redo()
		return legacy_manager as EditorUndoRedoManager
	return null

## Starts one undo action; callers add do/undo entries and then commit.
func _begin(action_name: String) -> EditorUndoRedoManager:
	var mgr: EditorUndoRedoManager = _undo_mgr()
	if mgr == null: return null
	mgr.create_action(action_name)
	return mgr

func _commit(mgr: EditorUndoRedoManager) -> void:
	if mgr != null: mgr.commit_action()

func _log(method: String, extra: Dictionary = {}) -> void:
	if _plugin == null or not _plugin.has_method("log_event"): return
	var entry := {"method": method, "ok": true}
	for key in extra.keys(): entry[key] = extra[key]
	_plugin.log_event(entry)

func _has_property(node: Node, prop: String) -> bool:
	for info in node.get_property_list():
		if String(info.get("name", "")) == prop: return true
	return false

func _prop_missing(path: String, node: Node, prop: String) -> Dictionary:
	return _err(1003, "property '%s' not found on %s" % [prop, node.get_class()],
		"Call godot_list_properties with path '%s' to see the available properties." % path,
		{"path": path, "property": prop})

## Builds the 1004 envelope for a failed decode.
##
## The decoder returns a structured error Dictionary; it must never be turned
## into a String (that raised inside the old code and turned the whole response
## into an empty result payload).
func _codec_error(prop: String, current: Variant, dec: Dictionary) -> Dictionary:
	var expected := type_string(typeof(current))
	var detail := _codec_message(dec)
	return _err(1004, "cannot convert the value for property '%s' to %s: %s" % [prop, expected, detail],
		"Send a %s value such as %s, or call godot_get_property first."
			% [expected, _codec_example(expected)],
		{"property": prop, "expected_type": expected})


## Extracts the human readable message from a decoder failure.
func _codec_message(dec: Dictionary) -> String:
	var payload := McpErrors.payload(dec.get("error", null))
	if not payload.is_empty():
		var message := String(payload.get("message", ""))
		if not message.is_empty(): return message
	if typeof(dec.get("message", null)) == TYPE_STRING:
		var fallback := String(dec.get("message"))
		if not fallback.is_empty(): return fallback
	return "the value does not match the property type"


## Example payload for the hint of a 1004 failure (PROTOCOL.md §4).
func _codec_example(expected: String) -> String:
	match expected:
		"Vector2":
			return "{\"__gd\":\"Vector2\",\"x\":1,\"y\":2}"
		"Vector2i":
			return "{\"__gd\":\"Vector2i\",\"x\":1,\"y\":2}"
		"Vector3":
			return "{\"__gd\":\"Vector3\",\"x\":1,\"y\":2,\"z\":3}"
		"Vector3i":
			return "{\"__gd\":\"Vector3i\",\"x\":1,\"y\":2,\"z\":3}"
		"Vector4":
			return "{\"__gd\":\"Vector4\",\"x\":1,\"y\":2,\"z\":3,\"w\":4}"
		"Color":
			return "{\"__gd\":\"Color\",\"r\":1,\"g\":0,\"b\":0,\"a\":1}"
		"Rect2":
			return "{\"__gd\":\"Rect2\",\"position\":{\"__gd\":\"Vector2\",\"x\":0,\"y\":0},\"size\":{\"__gd\":\"Vector2\",\"x\":16,\"y\":16}}"
		"Rect2i":
			return "{\"__gd\":\"Rect2i\",\"position\":{\"__gd\":\"Vector2i\",\"x\":0,\"y\":0},\"size\":{\"__gd\":\"Vector2i\",\"x\":16,\"y\":16}}"
		"Transform2D":
			return "{\"__gd\":\"Transform2D\",\"x\":{\"__gd\":\"Vector2\",\"x\":1,\"y\":0},\"y\":{\"__gd\":\"Vector2\",\"x\":0,\"y\":1},\"origin\":{\"__gd\":\"Vector2\",\"x\":0,\"y\":0}}"
		"Transform3D":
			return "{\"__gd\":\"Transform3D\",\"basis\":{\"__gd\":\"Basis\",\"x\":[1,0,0],\"y\":[0,1,0],\"z\":[0,0,1]},\"origin\":{\"__gd\":\"Vector3\",\"x\":0,\"y\":0,\"z\":0}}"
		"Basis":
			return "{\"__gd\":\"Basis\",\"x\":[1,0,0],\"y\":[0,1,0],\"z\":[0,0,1]}"
		"Quaternion":
			return "{\"__gd\":\"Quaternion\",\"x\":0,\"y\":0,\"z\":0,\"w\":1}"
		"Plane":
			return "{\"__gd\":\"Plane\",\"normal\":{\"__gd\":\"Vector3\",\"x\":0,\"y\":1,\"z\":0},\"d\":0}"
		"AABB":
			return "{\"__gd\":\"AABB\",\"position\":{\"__gd\":\"Vector3\",\"x\":0,\"y\":0,\"z\":0},\"size\":{\"__gd\":\"Vector3\",\"x\":1,\"y\":1,\"z\":1}}"
		"NodePath":
			return "{\"__gd\":\"NodePath\",\"path\":\"Player/Sprite2D\"}"
		"int":
			return "7"
		"float":
			return "0.5"
		"bool":
			return "true"
		"String":
			return "\"text\""
		_:
			return "{\"__gd\":\"%s\", ...}" % expected

func _sanitize_node_name(raw: String) -> String:
	var clean := ""
	for i in raw.length():
		var ch := raw.substr(i, 1)
		clean += "_" if ch in ["", ".", ":", "@", "/", "\"", "%"] else ch
	clean = clean.strip_edges()
	return "Node" if clean.is_empty() else clean

func _validate_scene_path(path: String) -> Dictionary:
	if path.is_empty(): return _missing("path")
	if not path.begins_with("res://"):
		return _err(1005, "scene path must start with res://, got '%s'" % path,
			"Use a project path such as res://scenes/Main.tscn.", {"path": path})
	if not path.ends_with(".tscn"):
		return _err(1005, "scene path must end with .tscn, got '%s'" % path,
			"Name the file like res://scenes/Main.tscn.", {"path": path})
	if path.contains(".."):
		return _err(1005, "scene path must not contain '..': '%s'" % path, "Pass a normalized res:// path.", {"path": path})
	return {}

func _validate_class(type_name: String, want_node := true) -> Dictionary:
	if not ClassDB.class_exists(type_name):
		return _err(1004, "unknown class '%s'" % type_name,
			"Call godot_search_classes (query 'Sprite') to find valid class names.", {"type": type_name})
	if not ClassDB.can_instantiate(type_name):
		return _err(1004, "class '%s' cannot be instantiated" % type_name,
			"Abstract classes cannot be used; pick a concrete class.", {"type": type_name})
	if want_node and type_name != "Node" and not ClassDB.is_parent_class(type_name, "Node"):
		return _err(1004, "class '%s' is not a Node" % type_name, "Only Node subclasses can be added to a scene.", {"type": type_name})
	return {}

# ---------------------------------------------------------------------- scene
func _scene_get_tree(p: Dictionary) -> Dictionary:
	var root := _edited_root()
	if root == null: return _no_scene()
	var counter := [0]
	var tree := _tree_of(root, _i(p, "depth", -1), _b(p, "include_internal"), _b(p, "with_props"), true, counter)
	return {"root": tree, "count": int(counter[0])}

func _tree_of(node: Node, depth: int, internal: bool, props: bool, is_root: bool, counter: Array) -> Dictionary:
	counter[0] = int(counter[0]) + 1
	var data := {"name": String(node.name), "type": node.get_class(), "path": _path_of(node),
		"script": _script_path(node), "owner": _path_of(node.owner), "children": []}
	if is_root and props: data["properties"] = _props_map(node, "")
	if depth == 0: return data
	var next := -1 if depth < 0 else depth - 1
	var kids: Array = []
	for child in node.get_children(internal):
		if child == null: continue
		if not internal and String(child.name).begins_with("@"): continue
		kids.append(_tree_of(child, next, internal, false, false, counter))
	data["children"] = kids
	return data

func _scene_current(_p: Dictionary) -> Dictionary:
	var root := _edited_root()
	if root == null: return {"path": "", "modified": false, "root_name": "", "root_type": ""}
	return {"path": String(root.scene_file_path), "modified": _scene_modified(),
		"root_name": String(root.name), "root_type": root.get_class()}

## Scene dirty flag, derived from EditorInterface.get_unsaved_scenes().
##
## Godot 4.7 has no `is_scene_modified()`; the shared helper probes the real API
## first and only falls back to `false` when the build exposes nothing at all.
func _scene_modified() -> bool:
	return McpEditorState.current_scene_modified()

func _scene_list_open(_p: Dictionary) -> Dictionary:
	var root := _edited_root()
	var current := String(root.scene_file_path) if root != null else ""
	var scenes: Array = []
	for path in EditorInterface.get_open_scenes():
		var scene_path := String(path)
		scenes.append({"path": scene_path, "current": scene_path == current,
			"modified": McpEditorState.is_scene_modified(scene_path)})
	return {"scenes": scenes}

func _scene_open(p: Dictionary) -> Dictionary:
	var path := _s(p, "path")
	if path.is_empty(): return _missing("path")
	if not path.begins_with("res://"):
		return _err(1005, "scene path must start with res://, got '%s'" % path,
			"Call godot_list_resources with dir 'res://' to find scene files.", {"path": path})
	if not FileAccess.file_exists(path):
		return _err(1006, "scene file not found: '%s'" % path,
			"Call godot_list_resources to find .tscn files, or build one with godot_create_scene.", {"path": path})
	EditorInterface.open_scene_from_path(path)
	var root := _edited_root()
	if root == null:
		return _err(-32603, "the editor did not load '%s'" % path,
			"Check the Godot output panel for import errors, then retry.", {"path": path})
	_log("scene.open", {"path": path})
	return {"path": path, "root_type": root.get_class()}

func _scene_save(p: Dictionary) -> Dictionary:
	var root := _edited_root()
	if root == null: return _no_scene()
	var path := _s(p, "path")
	if not path.is_empty(): return _save_scene_as(path)
	var current := String(root.scene_file_path)
	if current.is_empty():
		return _err(1005, "the current scene has never been saved, so there is no path to save to",
			"Call godot_save_scene with an explicit res:// path, or godot_save_scene_as.", {"path": ""})
	var err := EditorInterface.save_scene()
	if err != OK:
		return _err(-32603, "save_scene failed: %s (%d)" % [error_string(err), err],
			"Check the Godot output panel; the file may be read-only.", {"path": current})
	_log("scene.save", {"path": current})
	return {"saved": true, "path": current}

func _scene_save_as(p: Dictionary) -> Dictionary:
	var bad := _validate_scene_path(_s(p, "path"))
	return bad if not bad.is_empty() else _save_scene_as(_s(p, "path"))

func _save_scene_as(path: String) -> Dictionary:
	if _edited_root() == null: return _no_scene()
	if not EditorInterface.has_method("save_scene_as"):
		return _err(1014, "EditorInterface.save_scene_as() is unavailable in this Godot version",
			"Upgrade to Godot 4.3 or newer.", {"path": path})
	EditorInterface.save_scene_as(path, false)
	var root := _edited_root()
	var saved := String(root.scene_file_path) if root != null else path
	if saved != path:
		return _err(-32603, "the editor saved the scene as '%s' instead of '%s'" % [saved, path],
			"Check the Godot output panel for the save error and retry.", {"path": path})
	_log("scene.save", {"path": saved})
	return {"saved": true, "path": saved}

func _scene_create(p: Dictionary) -> Dictionary:
	var path := _s(p, "path")
	var bad := _validate_scene_path(path)
	if not bad.is_empty(): return bad
	if FileAccess.file_exists(path):
		return _err(1013, "scene file already exists: '%s'" % path,
			"Call godot_open_scene to load it, or pick another path.", {"path": path})
	var root_type := _s(p, "root_type", "Node")
	if root_type.is_empty(): root_type = "Node"
	var type_error := _validate_class(root_type)
	if not type_error.is_empty(): return type_error
	var root_name := _s(p, "root_name")
	if root_name.is_empty(): root_name = String(path.get_file().get_basename())
	var node: Node = ClassDB.instantiate(root_type)
	if node == null: return _err(1004, "could not instantiate '%s'" % root_type, "Call godot_get_class_info to check.", {"root_type": root_type})
	node.name = _sanitize_node_name(root_name)
	var packed := PackedScene.new()
	var pack_err := packed.pack(node)
	node.free()
	if pack_err != OK:
		return _err(-32603, "packing the new scene failed: %s" % error_string(pack_err), "Pick another root_type such as Node2D.", {"root_type": root_type})
	var save_err := ResourceSaver.save(packed, path)
	if save_err != OK:
		return _err(-32603, "saving '%s' failed: %s" % [path, error_string(save_err)],
			"Make sure the parent directory exists, then retry.", {"path": path})
	EditorInterface.open_scene_from_path(path)
	_log("scene.create", {"path": path})
	return {"path": path, "root_type": root_type}

# ----------------------------------------------------------------------- node
func _node_get(p: Dictionary) -> Dictionary:
	var path := _s(p, "path")
	var node := _resolve(path)
	if node == null: return _not_found(path)
	var groups: Array = []
	for group in node.get_groups(): groups.append(String(group))
	var result := {"name": String(node.name), "type": node.get_class(), "path": _path_of(node),
		"owner": _path_of(node.owner), "script": _script_path(node), "scene_file": String(node.scene_file_path),
		"groups": groups, "children_count": node.get_child_count()}
	if _b(p, "with_props"): result["properties"] = _props_map(node, "")
	return result

func _node_add(p: Dictionary) -> Dictionary:
	var type_name := _s(p, "type")
	if type_name.is_empty(): return _missing("type")
	var bad := _validate_class(type_name)
	if not bad.is_empty(): return bad
	var parent_path := _s(p, "parent_path")
	var parent := _resolve(parent_path)
	if parent == null: return _not_found(parent_path)
	var node: Node = ClassDB.instantiate(type_name)
	if node == null: return _err(1004, "instantiate('%s') returned null" % type_name, "Pick a concrete class.", {"type": type_name})
	var raw := _s(p, "name")
	node.name = _sanitize_node_name(raw if not raw.is_empty() else type_name)
	# Properties are applied on the detached instance so they travel with the undo payload.
	var prop_error := _apply_props(node, _dict(p, "properties"))
	if not prop_error.is_empty():
		node.free()
		return prop_error
	var mgr := _begin("MCP: node.add %s (%s)" % [String(node.name), type_name])
	if mgr == null:
		node.free()
		return _no_undo()
	mgr.add_do_method(parent, "add_child", node, true)
	mgr.add_do_method(node, "set_owner", _edited_root())
	var index := _i(p, "index", -1)
	if index >= 0: mgr.add_do_method(parent, "move_child", node, index)
	mgr.add_undo_method(parent, "remove_child", node)
	_commit(mgr)
	_log("node.add", {"path": _path_of(node), "type": type_name})
	return {"path": _path_of(node), "name": String(node.name), "type": node.get_class()}

## Validates every property first; restores the snapshot on the first failure.
func _apply_props(node: Object, props: Dictionary) -> Dictionary:
	var snapshot := {}
	for key in props.keys():
		var prop := String(key)
		var current: Variant = node.get(prop) if _has_property(node, prop) else null
		var dec := {"ok": false, "error": "unknown property"}
		if _has_property(node, prop): dec = McpValue.decode_checked(props[key], typeof(current))
		if not bool(dec.get("ok", false)):
			for done in snapshot.keys(): node.set(done, snapshot[done])
			if not _has_property(node, prop):
				return _err(1003, "property '%s' does not exist on %s" % [prop, node.get_class()],
					"Call godot_get_class_info with name '%s' to list its properties." % node.get_class(), {"property": prop})
			return _codec_error(prop, current, dec)
		snapshot[prop] = current
		node.set(prop, dec.get("value"))
	return {}

func _node_remove(p: Dictionary) -> Dictionary:
	var path := _s(p, "path")
	var node := _resolve(path)
	if node == null: return _not_found(path)
	if node == _edited_root(): return _err(1012, "the scene root cannot be removed", "Delete the file with godot_delete_resource.", {"path": path})
	var parent := node.get_parent()
	if parent == null: return _err(1002, "node '%s' has no parent and cannot be removed" % path, HINT_TREE, {"path": path})
	var index := node.get_index()
	var owner := node.owner
	var mgr := _begin("MCP: node.remove %s" % path)
	if mgr == null: return _no_undo()
	mgr.add_do_method(parent, "remove_child", node)
	mgr.add_undo_method(parent, "add_child", node)
	mgr.add_undo_method(parent, "move_child", node, index)
	mgr.add_undo_method(node, "set_owner", owner)
	_commit(mgr)
	_log("node.remove", {"path": path})
	return {"removed": true, "path": path}

func _node_rename(p: Dictionary) -> Dictionary:
	var path := _s(p, "path")
	var node := _resolve(path)
	if node == null: return _not_found(path)
	var raw := _s(p, "name")
	if raw.is_empty(): return _missing("name")
	var old_name := String(node.name)
	var new_name := _sanitize_node_name(raw)
	if new_name == old_name: return {"path": _path_of(node), "old_name": old_name}
	var mgr := _begin("MCP: node.rename %s" % path)
	if mgr == null: return _no_undo()
	mgr.add_do_property(node, "name", new_name)
	mgr.add_undo_property(node, "name", old_name)
	_commit(mgr)
	return {"path": _path_of(node), "old_name": old_name}

func _node_move(p: Dictionary) -> Dictionary:
	var path := _s(p, "path")
	var node := _resolve(path)
	if node == null: return _not_found(path)
	var new_parent_path := _s(p, "new_parent")
	var new_parent := _resolve(new_parent_path)
	if new_parent == null: return _not_found(new_parent_path)
	if node == new_parent or node.is_ancestor_of(new_parent):
		return _err(1012, "cannot move '%s' into its own subtree" % path,
			"Choose a parent that is not a descendant of the node.", {"path": path, "new_parent": new_parent_path})
	var old_parent := node.get_parent()
	if old_parent == null: return _err(1002, "node '%s' has no parent" % path, HINT_TREE, {"path": path})
	var old_index := node.get_index()
	var owner := node.owner
	var index := _i(p, "index", -1)
	var mgr := _begin("MCP: node.move %s -> %s" % [path, new_parent_path])
	if mgr == null: return _no_undo()
	mgr.add_do_method(old_parent, "remove_child", node)
	mgr.add_do_method(new_parent, "add_child", node, true)
	if index >= 0: mgr.add_do_method(new_parent, "move_child", node, index)
	mgr.add_do_method(node, "set_owner", _edited_root())
	mgr.add_undo_method(new_parent, "remove_child", node)
	mgr.add_undo_method(old_parent, "add_child", node, true)
	mgr.add_undo_method(old_parent, "move_child", node, old_index)
	mgr.add_undo_method(node, "set_owner", owner)
	_commit(mgr)
	return {"path": _path_of(node), "parent": _path_of(new_parent)}

func _node_duplicate(p: Dictionary) -> Dictionary:
	var path := _s(p, "path")
	var node := _resolve(path)
	if node == null: return _not_found(path)
	var parent := node.get_parent()
	if parent == null:
		return _err(1002, "node '%s' has no parent, so it cannot be duplicated in-place" % path,
			"Duplicate a child node instead, or reopen the scene.", {"path": path})
	var root := _edited_root()
	var copy: Node = node.duplicate()
	if copy == null: return _err(-32603, "duplicate() returned null for '%s'" % path, "Reopen the scene and retry.", {"path": path})
	var raw := _s(p, "name")
	copy.name = _sanitize_node_name(raw if not raw.is_empty() else String(node.name) + "Copy")
	var mgr := _begin("MCP: node.duplicate %s" % path)
	if mgr == null:
		copy.free()
		return _no_undo()
	mgr.add_do_method(parent, "add_child", copy, true)
	var pending: Array = [copy]
	var guard := 0
	while not pending.is_empty() and guard < 2000:
		var current: Node = pending.pop_back()
		mgr.add_do_method(current, "set_owner", root)
		for child in current.get_children(): pending.append(child)
		guard += 1
	mgr.add_undo_method(parent, "remove_child", copy)
	_commit(mgr)
	return {"path": _path_of(copy)}

func _node_find(p: Dictionary) -> Dictionary:
	var search_root := _resolve(_s(p, "root_path"))
	if search_root == null: search_root = _edited_root()
	if search_root == null: return _no_scene()
	var filter := {"pattern": _s(p, "pattern"), "type": _s(p, "type"), "group": _s(p, "group"), "script": _s(p, "script")}
	var out: Array = []
	_collect(search_root, filter, maxi(1, _i(p, "limit", 200)), out)
	return {"nodes": out, "count": out.size()}

func _collect(node: Node, filter: Dictionary, limit: int, out: Array) -> void:
	if out.size() >= limit: return
	if _matches(node, filter):
		out.append({"path": _path_of(node), "name": String(node.name), "type": node.get_class()})
	for child in node.get_children():
		if out.size() >= limit: return
		if String(child.name).begins_with("@"): continue
		_collect(child, filter, limit, out)

func _matches(node: Node, filter: Dictionary) -> bool:
	var type_name := String(filter.get("type", ""))
	if not type_name.is_empty() and not node.is_class(type_name): return false
	var group := String(filter.get("group", ""))
	if not group.is_empty() and not node.is_in_group(group): return false
	var script_filter := String(filter.get("script", ""))
	if not script_filter.is_empty() and _script_path(node) != script_filter: return false
	var pattern := String(filter.get("pattern", "")).to_lower()
	if pattern.is_empty(): return true
	var lowered := String(node.name).to_lower()
	return lowered == pattern or lowered.contains(pattern) or lowered.match(pattern)

func _node_call(p: Dictionary) -> Dictionary:
	var path := _s(p, "path")
	var node := _resolve(path)
	if node == null: return _not_found(path)
	var method_name := _s(p, "method")
	if method_name.is_empty(): return _missing("method")
	if not node.has_method(method_name):
		return _err(-32602, "method '%s' not found on %s" % [method_name, node.get_class()],
			"Call godot_get_class_info with name '%s' to list its methods." % node.get_class(), {"path": path, "method": method_name})
	var args: Array = []
	for value in _arr(p, "args"):
		var dec: Dictionary = McpValue.decode_checked(value, TYPE_NIL)
		args.append(dec.get("value") if bool(dec.get("ok", false)) else value)
	# Arbitrary side effects are not expressible as an undo step: the call runs directly and
	# only a reference is registered so the history entry stays valid.
	var mgr := _begin("MCP: node.call %s.%s" % [_path_of(node), method_name])
	if mgr == null: return _no_undo()
	mgr.add_undo_reference(node)
	_commit(mgr)
	return {"result": McpValue.encode(node.callv(method_name, args)), "undoable": false}

# ------------------------------------------------------------------- property
func _prop_list(p: Dictionary) -> Dictionary:
	var path := _s(p, "path")
	var node := _resolve(path)
	if node == null: return _not_found(path)
	var internal := _b(p, "include_internal")
	var with_values := _b(p, "with_values")
	var out: Array = []
	for info in node.get_property_list():
		var usage := int(info.get("usage", 0))
		if not (usage & PROPERTY_USAGE_STORAGE) and not (usage & PROPERTY_USAGE_SCRIPT_VARIABLE): continue
		var pname := String(info.get("name", ""))
		if pname.is_empty() or (not internal and pname.begins_with("_")): continue
		var hint_name := String(HINT_NAMES.get(int(info.get("hint", 0)), ""))
		if hint_name.is_empty(): hint_name = String(info.get("hint_string", ""))
		var entry := {"name": pname, "type": type_string(int(info.get("type", TYPE_NIL))), "hint": hint_name, "usage": usage}
		if with_values: entry["value"] = McpValue.encode(node.get(pname))
		out.append(entry)
	return {"properties": out}

func _prop_get(p: Dictionary) -> Dictionary:
	var path := _s(p, "path")
	var node := _resolve(path)
	if node == null: return _not_found(path)
	var prop := _s(p, "property")
	if prop.is_empty(): return _missing("property")
	if not _has_property(node, prop): return _prop_missing(path, node, prop)
	return {"value": McpValue.encode(node.get(prop))}

func _prop_get_all(p: Dictionary) -> Dictionary:
	var path := _s(p, "path")
	var node := _resolve(path)
	if node == null: return _not_found(path)
	return {"properties": _props_map(node, _s(p, "prefix"))}

func _props_map(node: Node, prefix: String) -> Dictionary:
	var out := {}
	for info in node.get_property_list():
		if not (int(info.get("usage", 0)) & PROPERTY_USAGE_STORAGE): continue
		var pname := String(info.get("name", ""))
		if pname.is_empty() or pname.begins_with("_") or pname == "script": continue
		if not prefix.is_empty() and not pname.begins_with(prefix): continue
		out[pname] = McpValue.encode(node.get(pname))
	return out

func _prop_set(p: Dictionary) -> Dictionary:
	var path := _s(p, "path")
	var node := _resolve(path)
	if node == null: return _not_found(path)
	var prop := _s(p, "property")
	if prop.is_empty(): return _missing("property")
	if not _has_property(node, prop): return _prop_missing(path, node, prop)
	var current: Variant = node.get(prop)
	var dec: Dictionary = McpValue.decode_checked(p.get("value"), typeof(current))
	if not bool(dec.get("ok", false)): return _codec_error(prop, current, dec)
	var mgr := _begin("MCP: prop.set %s.%s" % [_path_of(node), prop])
	if mgr == null: return _no_undo()
	mgr.add_do_property(node, prop, dec.get("value"))
	mgr.add_undo_property(node, prop, current)
	_commit(mgr)
	return {"old": McpValue.encode(current), "new": McpValue.encode(node.get(prop))}

func _prop_set_many(p: Dictionary) -> Dictionary:
	var path := _s(p, "path")
	var node := _resolve(path)
	if node == null: return _not_found(path)
	var values := _dict(p, "values")
	if values.is_empty(): return _missing("values")
	var olds := {}
	var news := {}
	for key in values.keys():
		var prop := String(key)
		if not _has_property(node, prop): return _prop_missing(path, node, prop)
		var current: Variant = node.get(prop)
		var dec: Dictionary = McpValue.decode_checked(values[key], typeof(current))
		if not bool(dec.get("ok", false)): return _codec_error(prop, current, dec)
		olds[prop] = current
		news[prop] = dec.get("value")
	var mgr := _begin("MCP: prop.set_many %s (%d props)" % [_path_of(node), news.size()])
	if mgr == null: return _no_undo()
	for prop in news.keys():
		mgr.add_do_property(node, prop, news[prop])
		mgr.add_undo_property(node, prop, olds[prop])
	_commit(mgr)
	return {"applied": news.size(), "old": McpValue.encode(olds)}

func _prop_reset(p: Dictionary) -> Dictionary:
	var path := _s(p, "path")
	var node := _resolve(path)
	if node == null: return _not_found(path)
	var prop := _s(p, "property")
	if prop.is_empty(): return _missing("property")
	if not _has_property(node, prop): return _prop_missing(path, node, prop)
	var current: Variant = node.get(prop)
	var revert_value: Variant = node.property_get_revert(prop)
	if revert_value == null and not node.property_can_revert(prop):
		return _err(1003, "property '%s' has no default value to reset to" % prop,
			"Use godot_set_property with an explicit value instead.", {"path": path, "property": prop})
	var mgr := _begin("MCP: prop.reset %s.%s" % [_path_of(node), prop])
	if mgr == null: return _no_undo()
	mgr.add_do_property(node, prop, revert_value)
	mgr.add_undo_property(node, prop, current)
	_commit(mgr)
	return {"value": McpValue.encode(node.get(prop))}

# -------------------------------------------------------------------- signal
func _signal_list(p: Dictionary) -> Dictionary:
	var path := _s(p, "path")
	var node := _resolve(path)
	if node == null: return _not_found(path)
	var internal := _b(p, "include_internal")
	var out: Array = []
	for info in node.get_signal_list():
		var sname := String(info.get("name", ""))
		var args: Array = []
		for arg in info.get("args", []):
			args.append({"name": String(arg.get("name", "")), "type": type_string(int(arg.get("type", TYPE_NIL)))})
		out.append({"name": sname, "args": args, "connections": _connections_of(node, sname, internal)})
	return {"signals": out}

## Lists the connections of one signal.
##
## Editor-internal connections (targets or methods containing "@", e.g. the
## inspector's `@Importer@123` handlers) are noise for an agent, so they are
## filtered unless `include_internal` is true (PROTOCOL.md §3.5).
func _connections_of(node: Node, signal_name: String, include_internal := false) -> Array:
	var out: Array = []
	for conn in node.get_signal_connection_list(signal_name):
		var callable: Callable = conn.get("callable", Callable())
		var target: Object = callable.get_object()
		var target_path := _path_of(target) if target is Node else (String(target.get_class()) if target != null else "")
		var method_name := String(callable.get_method())
		if not include_internal and (target_path.contains("@") or method_name.contains("@")):
			continue
		out.append({"target": target_path, "method": method_name, "flags": int(conn.get("flags", 0))})
	return out

func _parse_flags(raw: Variant) -> int:
	var names: Array = raw if raw is Array else ([raw] if raw is String else [])
	var flags := 0
	for entry in names:
		match String(entry).to_lower():
			"one_shot": flags |= CONNECT_ONE_SHOT
			"deferred": flags |= CONNECT_DEFERRED
			"persist": flags |= CONNECT_PERSIST
			"reference_counted": flags |= CONNECT_REFERENCE_COUNTED
	return flags

func _has_signal(node: Node, signal_name: String) -> bool:
	for info in node.get_signal_list():
		if String(info.get("name", "")) == signal_name: return true
	return false

## Resolves signal + target + handler once; returns [info, error] (one of them null).
func _signal_target(p: Dictionary, node: Node, path: String) -> Array:
	var signal_name := _s(p, "signal")
	if signal_name.is_empty(): return [null, _missing("signal")]
	if not _has_signal(node, signal_name):
		return [null, _err(1003, "signal '%s' not found on %s" % [signal_name, node.get_class()],
			"Call godot_list_signals with path '%s' to list its signals." % path, {"path": path, "signal": signal_name})]
	var target_path := _s(p, "target_path")
	var target := _resolve(target_path)
	if target == null: return [null, _not_found(target_path)]
	var method_name := _s(p, "method")
	if method_name.is_empty(): return [null, _missing("method")]
	return [{"signal": signal_name, "target": target, "target_path": target_path,
		"method": method_name, "callable": Callable(target, method_name)}, {}]

func _signal_connect(p: Dictionary) -> Dictionary:
	var path := _s(p, "path")
	var node := _resolve(path)
	if node == null: return _not_found(path)
	var resolved := _signal_target(p, node, path)
	if resolved[0] == null: return resolved[1]
	var info: Dictionary = resolved[0]
	var callable: Callable = info["callable"]
	if not (info["target"] as Node).has_method(info["method"]):
		return _err(-32602, "method '%s' not found on '%s'" % [info["method"], info["target_path"]],
			"Attach the handler script first, then retry.", {"target_path": info["target_path"], "method": info["method"]})
	if node.is_connected(info["signal"], callable):
		return _err(1012, "signal '%s' is already connected to %s.%s" % [info["signal"], info["target_path"], info["method"]],
			"Call godot_disconnect_signal first if you need different flags.",
			{"path": path, "signal": info["signal"], "method": info["method"]})
	var mgr := _begin("MCP: signal.connect %s.%s" % [path, info["signal"]])
	if mgr == null: return _no_undo()
	mgr.add_do_method(node, "connect", info["signal"], callable, _parse_flags(p.get("flags", [])))
	mgr.add_undo_method(node, "disconnect", info["signal"], callable)
	_commit(mgr)
	if not node.is_connected(info["signal"], callable):
		return _err(1012, "connect() failed for signal '%s' -> %s.%s" % [info["signal"], info["target_path"], info["method"]],
			"Check that the handler argument count matches the signal signature.",
			{"path": path, "signal": info["signal"], "method": info["method"]})
	return {"connected": true}

func _signal_disconnect(p: Dictionary) -> Dictionary:
	var path := _s(p, "path")
	var node := _resolve(path)
	if node == null: return _not_found(path)
	var resolved := _signal_target(p, node, path)
	if resolved[0] == null: return resolved[1]
	var info: Dictionary = resolved[0]
	var callable: Callable = info["callable"]
	if not node.is_connected(info["signal"], callable):
		return _err(1003, "signal '%s' is not connected to %s.%s" % [info["signal"], info["target_path"], info["method"]],
			"Call godot_list_signals with path '%s' to see existing connections." % path,
			{"path": path, "signal": info["signal"], "method": info["method"]})
	var flags := 0
	for conn in node.get_signal_connection_list(info["signal"]):
		if (conn.get("callable", Callable()) as Callable) == callable: flags = int(conn.get("flags", 0))
	var mgr := _begin("MCP: signal.disconnect %s.%s" % [path, info["signal"]])
	if mgr == null: return _no_undo()
	mgr.add_do_method(node, "disconnect", info["signal"], callable)
	mgr.add_undo_method(node, "connect", info["signal"], callable, flags)
	_commit(mgr)
	return {"disconnected": true}

# -------------------------------------------------------------------- editor
func _editor_get_selection(_p: Dictionary) -> Dictionary:
	var selection := EditorInterface.get_selection()
	var out: Array = []
	if selection != null:
		for node in selection.get_selected_nodes():
			if node == null: continue
			out.append({"path": _path_of(node), "name": String(node.name), "type": node.get_class()})
	return {"nodes": out}

func _editor_set_selection(p: Dictionary) -> Dictionary:
	var selection := EditorInterface.get_selection()
	if selection == null: return _err(1014, "the editor selection API is not available", "Upgrade Godot and retry.")
	selection.clear()
	var count := 0
	var first: Node = null
	for entry in _arr(p, "paths"):
		var node := _resolve(String(entry))
		if node == null: continue
		selection.add_node(node)
		if first == null: first = node
		count += 1
	if _b(p, "inspect") and first != null: EditorInterface.inspect_object(first)
	return {"count": count}

## Returns the UndoRedo history that owns edits of the current scene.
##
## EditorUndoRedoManager itself only exposes create_action/add_*; undo/redo and
## history inspection live on the per-scene UndoRedo returned by
## get_history_undo_redo(). Falling back to GLOBAL_HISTORY keeps the tool usable
## when no scene is open.
func _history() -> UndoRedo:
	var mgr: EditorUndoRedoManager = _undo_mgr()
	if mgr == null: return null
	var root := EditorInterface.get_edited_scene_root()
	if root != null:
		var history_id := mgr.get_object_history_id(root)
		if history_id >= 0 and history_id != EditorUndoRedoManager.GLOBAL_HISTORY:
			return mgr.get_history_undo_redo(history_id)
	return mgr.get_history_undo_redo(EditorUndoRedoManager.GLOBAL_HISTORY)

func _editor_undo(p: Dictionary) -> Dictionary:
	var history = _history()
	if history == null: return _no_undo()
	var done := 0
	for i in maxi(1, _i(p, "steps", 1)):
		if not history.has_undo(): break
		history.undo()
		done += 1
	return {"done": done} if done > 0 else _err(1009, "nothing to undo", "The active scene has no undo history. Make a change through a tool first.")

func _editor_redo(p: Dictionary) -> Dictionary:
	var history = _history()
	if history == null: return _no_undo()
	var done := 0
	for i in maxi(1, _i(p, "steps", 1)):
		if not history.has_redo(): break
		history.redo()
		done += 1
	return {"done": done} if done > 0 else _err(1009, "nothing to redo", "The active scene has no redo history.")

## Undo history of the current scene (PROTOCOL.md §3.8).
##
## UndoRedo exposes `get_history_count()` + `get_action_name(i)`, which is the
## full stack - the newest action is at index count-1, so it is reported first.
## At most MAX_UNDO_HISTORY entries are returned; mergeable is always false
## because UndoRedo does not expose the merge flag.
func _editor_undo_history(_p: Dictionary) -> Dictionary:
	var actions: Array = []
	var history = _history()
	if history == null: return {"actions": actions}
	var count := int(history.get_history_count())
	var first := maxi(0, count - MAX_UNDO_HISTORY)
	for index in range(count - 1, first - 1, -1):
		actions.append({"name": String(history.get_action_name(index)), "mergeable": false})
	return {"actions": actions}

func _editor_inspect(p: Dictionary) -> Dictionary:
	var path := _s(p, "path")
	var node := _resolve(path)
	if node == null: return _not_found(path)
	EditorInterface.inspect_object(node)
	return {"inspected": true}

func _editor_open_script(p: Dictionary) -> Dictionary:
	var path := _s(p, "path")
	if path.is_empty(): return _missing("path")
	if not FileAccess.file_exists(path):
		return _err(1006, "script not found: '%s'" % path, "Call godot_list_scripts to find existing scripts.", {"path": path})
	var res = load(path)
	if not (res is Script): return _err(1004, "'%s' is not a script resource" % path, "Pass a .gd file path.", {"path": path})
	if not EditorInterface.has_method("edit_script"):
		return _err(1014, "EditorInterface.edit_script() is unavailable", "Upgrade to Godot 4.3 or newer.", {"path": path})
	EditorInterface.edit_script(res, maxi(0, _i(p, "line")))
	return {"opened": true}

func _editor_project_settings(p: Dictionary) -> Dictionary:
	var prefix := _s(p, "prefix")
	var out := {}
	for info in ProjectSettings.get_property_list():
		var key := String(info.get("name", ""))
		if key.is_empty() or not key.contains("/"): continue
		if int(info.get("usage", 0)) & PROPERTY_USAGE_INTERNAL: continue
		if not prefix.is_empty() and not key.begins_with(prefix): continue
		out[key] = McpValue.encode(ProjectSettings.get_setting(key))
	return {"settings": out}

func _editor_set_project_setting(p: Dictionary) -> Dictionary:
	var key := _s(p, "key")
	if key.is_empty(): return _missing("key")
	if not ProjectSettings.has_setting(key):
		return _err(1003, "project setting '%s' not found" % key,
			"Call godot_get_project_settings with a prefix to list valid keys.", {"key": key})
	var current: Variant = ProjectSettings.get_setting(key)
	var dec: Dictionary = McpValue.decode_checked(p.get("value"), typeof(current))
	if not bool(dec.get("ok", false)): return _codec_error(key, current, dec)
	var mgr := _begin("MCP: editor.set_project_setting %s" % key)
	if mgr == null: return _no_undo()
	mgr.add_do_method(ProjectSettings, "set_setting", key, dec.get("value"))
	mgr.add_do_method(ProjectSettings, "save")
	mgr.add_undo_method(ProjectSettings, "set_setting", key, current)
	_commit(mgr)
	return {"key": key, "old": McpValue.encode(current)}

func _editor_autoloads(_p: Dictionary) -> Dictionary:
	var out: Array = []
	for info in ProjectSettings.get_property_list():
		var key := String(info.get("name", ""))
		if not key.begins_with("autoload/"): continue
		var raw := String(ProjectSettings.get_setting(key))
		out.append({"name": key.trim_prefix("autoload/"), "path": raw.trim_prefix("*"), "enabled": raw.begins_with("*")})
	return {"autoloads": out}
