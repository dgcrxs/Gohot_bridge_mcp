@tool
extends RefCounted
## Execution layer (PROTOCOL.md §3.12): runs a GDScript snippet inside the edited
## scene by wrapping it in a throwaway Node that defines `_mcp_run()`.

const McpErrors = preload("res://addons/godot_mcp/core/errors.gd")
const McpValue = preload("res://addons/godot_mcp/core/value_codec.gd")

const WRAPPER_HEADER := "@tool\nextends Node\n\nfunc _mcp_run():\n"
const WRAPPER_FALLBACK := "\treturn null\n"
const TAB := "\t"

var _plugin: Object = null
var _re_run: RegEx = null
var _re_extends: RegEx = null


func _init(plugin: Object) -> void:
	_plugin = plugin
	_re_run = RegEx.create_from_string("(?m)^\\s*func\\s+_mcp_run\\s*\\(")
	_re_extends = RegEx.create_from_string("(?m)^\\s*extends\\s+")


## Routes one RPC method; returns the result payload or an McpErrors failure.
func handle(action: String, params: Dictionary) -> Dictionary:
	if action == "execute.gdscript":
		return _execute(params)
	return McpErrors.fail(-32601, "method '%s' is not handled by execute_ops" % action,
		"Call get_capabilities to see the supported method list.", {"method": action})


func _s(p: Dictionary, key: String, def := "") -> String:
	return String(p.get(key, def))


## Runs the snippet and reports its return value.
##
## Args:
##   params: {"code": String, "base_path"?: String, "timeout_ms"?: int}
##
## Returns:
##   {result, output, elapsed_ms} (plus "note" when the value needed a string fallback)
func _execute(params: Dictionary) -> Dictionary:
	var code := _s(params, "code")
	if code.strip_edges().is_empty():
		return McpErrors.fail(-32602, "missing required param 'code'",
			"Send the statements to run, e.g. {\"code\":\"return get_child_count()\"}.", {"param": "code"})
	var base := _resolve(_s(params, "base_path"))
	if base == null:
		return _base_error(_s(params, "base_path"))
	var script := GDScript.new()
	script.source_code = _build_source(code)
	var err := script.reload()
	if err != OK:
		return McpErrors.fail(1007, error_string(err),
			"Check the snippet syntax; the wrapper expects statements, not a full script.",
			{"code": code})
	var temp := Node.new()
	temp.set_script(script)
	base.add_child(temp)
	var started := Time.get_ticks_usec()
	var returned: Variant = null
	if temp.has_method("_mcp_run"):
		returned = temp.call("_mcp_run")
	var elapsed := int((Time.get_ticks_usec() - started) / 1000)
	# Remove before freeing so the editor tree is never left with a half-freed node.
	base.remove_child(temp)
	temp.call_deferred("queue_free")
	var result := {"result": null, "output": "", "elapsed_ms": elapsed}
	if returned == null: return result
	if _encodable(returned):
		result["result"] = McpValue.encode(returned)
		return result
	result["result"] = str(returned)
	result["note"] = "result was not serializable"
	return result


## Wraps a statement block into a Node script; a full `func _mcp_run()` is used as-is.
func _build_source(code: String) -> String:
	var body := code.replace("\r\n", "\n").replace("\r", "\n")
	if _re_run.search(body) != null:
		var full := body
		if _re_extends.search(full) == null:
			full = "@tool\nextends Node\n\n" + full
		elif not full.strip_edges().begins_with("@tool"):
			full = "@tool\n" + full
		return full
	var indented := ""
	for line in body.split("\n"):
		indented += "\n" if line.strip_edges().is_empty() else TAB + line + "\n"
	return WRAPPER_HEADER + (indented if not indented.strip_edges().is_empty() else WRAPPER_FALLBACK)


## True when the value can travel through the JSON value codec.
func _encodable(value: Variant) -> bool:
	match typeof(value):
		TYPE_CALLABLE, TYPE_SIGNAL, TYPE_RID:
			return false
		TYPE_OBJECT:
			return value is Node or value is Resource
	return true


## Resolves the snippet host: the scene root by default, or an explicit node path.
func _resolve(path: String) -> Node:
	var root := EditorInterface.get_edited_scene_root()
	var target := path.strip_edges()
	if target.is_empty() or target == ".": return root
	if target.contains(".."): return null
	if target.begins_with("/root/"):
		if root == null or not root.is_inside_tree(): return null
		return root.get_tree().root.get_node_or_null(target.trim_prefix("/root/"))
	if root == null: return null
	return root.get_node_or_null(target.trim_prefix("./"))


func _base_error(path: String) -> Dictionary:
	var root := EditorInterface.get_edited_scene_root()
	if root == null:
		return McpErrors.fail(1001, "no scene is currently open in the editor",
			"Call godot_create_scene or godot_open_scene to load a scene first.",
			{"base_path": path})
	return McpErrors.fail(1002, "base node not found: '%s'" % path,
		"Call godot_get_scene_tree to list valid node paths.", {"base_path": path})
