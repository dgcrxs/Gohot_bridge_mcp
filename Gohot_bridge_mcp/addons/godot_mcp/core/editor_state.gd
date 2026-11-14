@tool
extends RefCounted

## Editor scene-state helpers shared by `editor_ops.gd` and `rpc_router.gd`.
##
## Godot 4.7 does **not** expose `EditorInterface.is_scene_modified()`, so the
## dirty flag is derived from `EditorInterface.get_unsaved_scenes()`. Every
## place that reports "modified" (`scene.current`, `scene.list_open` and
## `get_editor_info`) calls this helper, so the three answers can never
## disagree (PROTOCOL.md §3.1 / §3.2).
##
## Usage:
##   var dirty: bool = McpEditorState.current_scene_modified()
##   var dirty_other: bool = McpEditorState.is_scene_modified("res://main.tscn")

## Paths of every scene the editor currently holds unsaved changes for.
##
## Returns:
##   Array[String] of `res://` scene paths; empty when nothing is dirty or when
##   the running Godot build exposes no unsaved-scene API at all.
static func unsaved_scenes() -> Array:
	var out: Array = []
	var editor := _editor()
	if editor == null:
		return out
	if editor.has_method("get_unsaved_scenes"):
		var raw: Variant = editor.call("get_unsaved_scenes")
		if typeof(raw) == TYPE_ARRAY or typeof(raw) == TYPE_PACKED_STRING_ARRAY:
			for item in raw:
				out.append(String(item))
		return out
	# Older 4.x minors: probe the single edited scene instead of the list.
	for method_name in ["is_scene_modified", "get_scene_modified"]:
		if not editor.has_method(method_name):
			continue
		if bool(editor.call(method_name)):
			var root := _edited_root(editor)
			if root != null:
				out.append(String(root.scene_file_path))
		break
	return out


## True when `scene_path` has unsaved changes in the editor.
##
## Args:
##   scene_path: A `res://` scene path, usually `Node.scene_file_path`.
##
## Returns:
##   bool - false for empty paths and for the API-less fallback.
static func is_scene_modified(scene_path: String) -> bool:
	if scene_path.is_empty():
		return false
	for path in unsaved_scenes():
		if String(path) == scene_path:
			return true
	return false


## True when the currently edited scene has unsaved changes.
static func current_scene_modified() -> bool:
	var root := _edited_root(_editor())
	if root == null:
		return false
	return is_scene_modified(String(root.scene_file_path))


## EditorInterface singleton, resolved through the engine so this file never
## needs an editor-only global at parse time.
static func _editor() -> Object:
	if not Engine.has_singleton("EditorInterface"):
		return null
	var singleton: Object = Engine.get_singleton("EditorInterface")
	return singleton


static func _edited_root(editor: Object) -> Node:
	if editor == null or not editor.has_method("get_edited_scene_root"):
		return null
	var root: Variant = editor.call("get_edited_scene_root")
	if root is Node:
		return root as Node
	return null
