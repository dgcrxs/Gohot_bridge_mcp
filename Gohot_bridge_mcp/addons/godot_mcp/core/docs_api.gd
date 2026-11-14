@tool
extends RefCounted
## Introspection layer (PROTOCOL.md §3.10): ClassDB reflection for models that need
## to discover class names, methods, properties, signals and enums.

const McpErrors = preload("res://addons/godot_mcp/core/errors.gd")

const MAX_METHODS := 300
const MAX_PROPERTIES := 300
const MAX_SIGNALS := 120
const DEFAULT_LIMIT := 50
const MAX_LIMIT := 500

var _plugin: Object = null


func _init(plugin: Object) -> void:
	_plugin = plugin


## Routes one RPC method; returns the result payload or an McpErrors failure.
func handle(action: String, params: Dictionary) -> Dictionary:
	match action:
		"docs.class":
			return _class_info(params)
		"docs.search":
			return _search(params)
	return McpErrors.fail(-32601, "method '%s' is not handled by docs_api" % action,
		"Call get_capabilities to see the supported method list.", {"method": action})


func _s(p: Dictionary, key: String, def := "") -> String:
	return String(p.get(key, def))


func _i(p: Dictionary, key: String, def := 0) -> int:
	return int(p.get(key, def))


# ------------------------------------------------------------------ docs.class
## Full reflection snapshot of one class.
##
## Args:
##   params: {"name": String}
##
## Returns:
##   {name, inherits, instantiable, methods, properties, signals, enums}
func _class_info(params: Dictionary) -> Dictionary:
	var name := _s(params, "name")
	if name.is_empty():
		return McpErrors.fail(-32602, "missing required param 'name'",
			"Example: {\"name\":\"Sprite2D\"}. Call godot_search_classes to discover names.", {"param": "name"})
	if not ClassDB.class_exists(name):
		return McpErrors.fail(1004, "unknown class '%s'" % name,
			"Call godot_search_classes with a partial name (for example 'Sprite') to find valid classes.",
			{"name": name})
	return {
		"name": name,
		"inherits": String(ClassDB.get_parent_class(name)),
		"instantiable": ClassDB.can_instantiate(name),
		"methods": _methods_of(name),
		"properties": _properties_of(name),
		"signals": _signals_of(name),
		"enums": _enums_of(name),
	}


## Public methods (inherited included), private ones filtered, capped at MAX_METHODS.
func _methods_of(cls_name: String) -> Array:
	var out: Array = []
	for info in ClassDB.class_get_method_list(cls_name, true):
		if out.size() >= MAX_METHODS: break
		var method_name := String(info.get("name", ""))
		if method_name.is_empty() or method_name.begins_with("_"): continue
		var args: Array = []
		for arg in info.get("args", []):
			args.append({"name": String(arg.get("name", "")), "type": _type_name(int(arg.get("type", TYPE_NIL)))})
		out.append({
			"name": method_name,
			"return_type": _type_name(int((info.get("return", {}) as Dictionary).get("type", TYPE_NIL))),
			"args": args,
		})
	return out


func _properties_of(cls_name: String) -> Array:
	var out: Array = []
	for info in ClassDB.class_get_property_list(cls_name, true):
		if out.size() >= MAX_PROPERTIES: break
		var prop_name := String(info.get("name", ""))
		if prop_name.is_empty() or prop_name.begins_with("_"): continue
		out.append({"name": prop_name, "type": _type_name(int(info.get("type", TYPE_NIL)))})
	return out


func _signals_of(cls_name: String) -> Array:
	var out: Array = []
	for info in ClassDB.class_get_signal_list(cls_name, true):
		if out.size() >= MAX_SIGNALS: break
		var args: Array = []
		for arg in info.get("args", []):
			args.append({"name": String(arg.get("name", "")), "type": _type_name(int(arg.get("type", TYPE_NIL)))})
		out.append({"name": String(info.get("name", "")), "args": args})
	return out


## Groups integer constants by their enum name; loose constants are skipped.
func _enums_of(cls_name: String) -> Array:
	var grouped := {}
	for constant in ClassDB.class_get_integer_constant_list(cls_name, true):
		var enum_name := String(ClassDB.class_get_integer_constant_enum(cls_name, String(constant)))
		if enum_name.is_empty(): continue
		if not grouped.has(enum_name): grouped[enum_name] = {}
		grouped[enum_name][String(constant)] = int(ClassDB.class_get_integer_constant(cls_name, String(constant)))
	var out: Array = []
	for key in grouped.keys():
		out.append({"name": String(key), "values": grouped[key]})
	return out


func _type_name(type_id: int) -> String:
	return "Nil" if type_id == TYPE_NIL else type_string(type_id)


# ----------------------------------------------------------------- docs.search
## Case-insensitive substring search over every registered class.
##
## Args:
##   params: {"query": String, "limit"?: int, "instantiable_only"?: bool}
##
## Returns:
##   {classes: [{name, inherits, instantiable}], count}
func _search(params: Dictionary) -> Dictionary:
	var query := _s(params, "query").strip_edges().to_lower()
	var limit := clampi(_i(params, "limit", DEFAULT_LIMIT), 1, MAX_LIMIT)
	var instantiable_only := bool(params.get("instantiable_only", false))
	var matches: Array = []
	for listed_class in ClassDB.get_class_list():
		var name := String(listed_class)
		if name.is_empty() or name.begins_with("_"): continue
		var can_create := ClassDB.can_instantiate(name)
		if query.is_empty():
			# Empty query is the legacy list_node_types path: node classes only.
			if not can_create or not ClassDB.is_parent_class(name, "Node"): continue
		else:
			if not name.to_lower().contains(query): continue
			if instantiable_only and not can_create: continue
		matches.append(name)
	matches.sort_custom(_shorter_first)
	var out: Array = []
	for name in matches:
		if out.size() >= limit: break
		out.append({"name": name, "inherits": String(ClassDB.get_parent_class(name)),
			"instantiable": ClassDB.can_instantiate(name)})
	return {"classes": out, "count": out.size()}


## Ranks short (more specific-sounding) names first, then alphabetically.
func _shorter_first(a: String, b: String) -> bool:
	if a.length() != b.length(): return a.length() < b.length()
	return a < b
