@tool
extends RefCounted

## McpCodec - bidirectional value codec for PROTOCOL.md §4.
##
## Encoding turns any Variant into a JSON-safe structure. Godot native types
## become `{"__gd": "<Type>", ...}` objects, native JSON types pass through.
## Encoding is depth limited (MAX_DEPTH) and never walks Node/Resource graphs.
##
## Decoding is permissive: exact `{"__gd": ...}` form, constructor-like strings
## ("Vector2(1, 2)", "(1, 2)", "1,2"), numeric arrays, colours ("#ff0000",
## "red"), and scalar string<->number/bool coercion.
##
## SECURITY: `str_to_var()` is deliberately never used - it evaluates GDScript
## expressions and would allow arbitrary code execution from remote input.
##
## Usage:
##   var encoded: Variant = McpCodec.encode(node.get("position"))
##   var checked: Dictionary = McpCodec.decode_checked(input, TYPE_VECTOR2)
##   if not checked["ok"]: return checked["error"]

const McpErrors = preload("res://addons/godot_mcp/core/errors.gd")

## PROTOCOL.md §4: encoding must be depth-limited (max 32).
const MAX_DEPTH := 32
const DEPTH_MARK := "<max-depth>"

## Type-name -> Variant type, used when parsing constructor-like strings.
const TYPE_NAMES := {
	"Vector2": TYPE_VECTOR2,
	"Vector2i": TYPE_VECTOR2I,
	"Vector3": TYPE_VECTOR3,
	"Vector3i": TYPE_VECTOR3I,
	"Vector4": TYPE_VECTOR4,
	"Color": TYPE_COLOR,
	"Rect2": TYPE_RECT2,
	"Rect2i": TYPE_RECT2I,
	"Transform2D": TYPE_TRANSFORM2D,
	"Transform3D": TYPE_TRANSFORM3D,
	"Basis": TYPE_BASIS,
	"Quaternion": TYPE_QUATERNION,
	"Plane": TYPE_PLANE,
	"AABB": TYPE_AABB,
	"NodePath": TYPE_NODE_PATH,
}

const TRUE_WORDS := ["true", "1", "yes", "on"]
const FALSE_WORDS := ["false", "0", "no", "off"]

## Default failure code (1004 = type/codec mismatch, PROTOCOL.md §5).
const ERR_CODEC := 1004

const PACKED_TYPES := [
	TYPE_PACKED_BYTE_ARRAY,
	TYPE_PACKED_INT32_ARRAY,
	TYPE_PACKED_INT64_ARRAY,
	TYPE_PACKED_FLOAT32_ARRAY,
	TYPE_PACKED_FLOAT64_ARRAY,
	TYPE_PACKED_STRING_ARRAY,
	TYPE_PACKED_VECTOR2_ARRAY,
	TYPE_PACKED_VECTOR3_ARRAY,
	TYPE_PACKED_COLOR_ARRAY,
]


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

## Encodes any Variant into JSON-safe data.
##
## Args:
##   value: Value to encode.
##   depth: Internal recursion counter; callers must not pass this.
##
## Returns:
##   A JSON-safe Variant. Nodes/Resources become {"__gd": "Object", ...}.
static func encode(value: Variant, depth: int = 0) -> Variant:
	if depth > MAX_DEPTH:
		return DEPTH_MARK
	var value_type := typeof(value)
	if PACKED_TYPES.has(value_type):
		return _encode_sequence(value, depth)
	match value_type:
		TYPE_NIL, TYPE_BOOL, TYPE_INT, TYPE_STRING:
			return value
		TYPE_FLOAT:
			var number: float = value
			if is_nan(number):
				return "<nan>"
			if is_inf(number):
				return "<inf>" if number > 0.0 else "<-inf>"
			return number
		TYPE_STRING_NAME:
			return String(value)
		TYPE_NODE_PATH:
			var node_path: NodePath = value
			return {"__gd": "NodePath", "path": String(node_path)}
		TYPE_VECTOR2:
			var v2: Vector2 = value
			return {"__gd": "Vector2", "x": v2.x, "y": v2.y}
		TYPE_VECTOR2I:
			var v2i: Vector2i = value
			return {"__gd": "Vector2i", "x": v2i.x, "y": v2i.y}
		TYPE_VECTOR3:
			var v3: Vector3 = value
			return {"__gd": "Vector3", "x": v3.x, "y": v3.y, "z": v3.z}
		TYPE_VECTOR3I:
			var v3i: Vector3i = value
			return {"__gd": "Vector3i", "x": v3i.x, "y": v3i.y, "z": v3i.z}
		TYPE_VECTOR4:
			var v4: Vector4 = value
			return {"__gd": "Vector4", "x": v4.x, "y": v4.y, "z": v4.z, "w": v4.w}
		TYPE_COLOR:
			var color: Color = value
			return {"__gd": "Color", "r": color.r, "g": color.g, "b": color.b, "a": color.a}
		TYPE_RECT2:
			var rect: Rect2 = value
			return {"__gd": "Rect2", "position": encode(rect.position, depth + 1), "size": encode(rect.size, depth + 1)}
		TYPE_RECT2I:
			var recti: Rect2i = value
			return {"__gd": "Rect2i", "position": encode(recti.position, depth + 1), "size": encode(recti.size, depth + 1)}
		TYPE_TRANSFORM2D:
			var t2: Transform2D = value
			return {
				"__gd": "Transform2D",
				"x": encode(t2.x, depth + 1),
				"y": encode(t2.y, depth + 1),
				"origin": encode(t2.origin, depth + 1),
			}
		TYPE_TRANSFORM3D:
			var t3: Transform3D = value
			return {
				"__gd": "Transform3D",
				"basis": encode(t3.basis, depth + 1),
				"origin": encode(t3.origin, depth + 1),
			}
		TYPE_BASIS:
			var basis: Basis = value
			return {
				"__gd": "Basis",
				"x": [basis.x.x, basis.x.y, basis.x.z],
				"y": [basis.y.x, basis.y.y, basis.y.z],
				"z": [basis.z.x, basis.z.y, basis.z.z],
			}
		TYPE_QUATERNION:
			var quat: Quaternion = value
			return {"__gd": "Quaternion", "x": quat.x, "y": quat.y, "z": quat.z, "w": quat.w}
		TYPE_PLANE:
			var plane: Plane = value
			return {"__gd": "Plane", "normal": encode(plane.normal, depth + 1), "d": plane.d}
		TYPE_AABB:
			var box: AABB = value
			return {"__gd": "AABB", "position": encode(box.position, depth + 1), "size": encode(box.size, depth + 1)}
		TYPE_RID:
			var rid: RID = value
			return {"__gd": "RID", "id": str(rid.get_id())}
		TYPE_OBJECT:
			return _encode_object(value)
		TYPE_ARRAY:
			var items: Array = value
			var out: Array = []
			for item in items:
				out.append(encode(item, depth + 1))
			return out
		TYPE_DICTIONARY:
			var source: Dictionary = value
			var mapped: Dictionary = {}
			for key in source.keys():
				mapped[_key_to_string(key)] = encode(source[key], depth + 1)
			return mapped
		TYPE_CALLABLE:
			return "<callable>"
		TYPE_SIGNAL:
			return "<signal>"
		_:
			# Projection and future Variant types: safe string fallback.
			return str(value)


## Encodes a packed array into a plain JSON array.
static func _encode_sequence(value: Variant, depth: int) -> Array:
	var out: Array = []
	var count: int = int(value.size())
	for index in range(count):
		out.append(encode(value[index], depth + 1))
	return out


## Encodes object references only - never recurses into the node/resource graph.
static func _encode_object(value: Variant) -> Variant:
	if value == null:
		return null
	var object: Object = value
	var klass := "Object"
	if object.has_method("get_class"):
		klass = String(object.call("get_class"))
	var path := ""
	if object is Resource:
		path = String((object as Resource).resource_path)
	elif object is Node:
		path = _node_relative_path(object as Node)
	return {"__gd": "Object", "class": klass, "path": path}


## Node path relative to the edited scene root (PROTOCOL.md §4).
static func _node_relative_path(node: Node) -> String:
	var tree: SceneTree = node.get_tree()
	if tree != null and tree.edited_scene_root != null:
		var root: Node = tree.edited_scene_root
		if node == root:
			return "."
		if root.is_ancestor_of(node):
			return String(root.get_path_to(node))
	if node.is_inside_tree():
		return String(node.get_path())
	return String(node.name)


## JSON object keys must be strings.
static func _key_to_string(key: Variant) -> String:
	if typeof(key) == TYPE_STRING_NAME:
		return String(key)
	return str(key)


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------

## Decodes a value, returning null on failure. Prefer decode_checked().
static func decode(value: Variant, expected_type: int = TYPE_NIL) -> Variant:
	var result := decode_checked(value, expected_type)
	return result.get("value", null)


## Decodes a value and reports failure explicitly.
##
## Returns:
##   {"ok": true, "value": Variant} or
##   {"ok": false, "error": {"__error": true, "error": {...}}, "code": int, "message": String}
static func decode_checked(value: Variant, expected_type: int = TYPE_NIL) -> Dictionary:
	var raw := _decode_any(value, expected_type, 0)
	if not bool(raw.get("ok", false)):
		return raw
	return _coerce(raw.get("value", null), expected_type)


## Walks the JSON structure, honouring `{"__gd": ...}` markers.
static func _decode_any(value: Variant, expected_type: int, depth: int) -> Dictionary:
	if depth > MAX_DEPTH:
		return _error("value nests deeper than %d levels" % MAX_DEPTH, {"type": type_string(typeof(value))})
	match typeof(value):
		TYPE_NIL:
			return _ok(null)
		TYPE_DICTIONARY:
			var data: Dictionary = value
			if data.has("__gd"):
				return _decode_gd(data, expected_type, depth)
			var mapped: Dictionary = {}
			for key in data.keys():
				var item := _decode_any(data[key], TYPE_NIL, depth + 1)
				mapped[key] = item.get("value", null) if bool(item.get("ok", false)) else null
			return _ok(mapped)
		TYPE_ARRAY:
			return _decode_array(value, expected_type, depth)
		TYPE_STRING:
			return _decode_string(String(value), expected_type)
		_:
			return _ok(value)


## Decodes a plain JSON array, then converts it for vector-like expectations.
static func _decode_array(value: Variant, expected_type: int, depth: int) -> Dictionary:
	var items: Array = value
	var out: Array = []
	for item in items:
		var decoded := _decode_any(item, TYPE_NIL, depth + 1)
		out.append(decoded.get("value", null) if bool(decoded.get("ok", false)) else null)
	if expected_type == TYPE_NIL or expected_type == TYPE_ARRAY:
		return _ok(out)
	return _from_components(out, expected_type)


## Decodes the exact `{"__gd": "<Type>", ...}` form.
static func _decode_gd(data: Dictionary, expected_type: int, depth: int) -> Dictionary:
	var tag := String(data.get("__gd", ""))
	match tag:
		"Vector2":
			var c2 := _vector_components(data, 2, "Vector2")
			if not bool(c2.get("ok", false)): return c2
			var v2c: Array = c2["value"]
			return _ok(Vector2(v2c[0], v2c[1]))
		"Vector2i":
			var ci2 := _vector_components(data, 2, "Vector2i")
			if not bool(ci2.get("ok", false)): return ci2
			var v2i_c: Array = ci2["value"]
			return _ok(Vector2i(int(round(v2i_c[0])), int(round(v2i_c[1]))))
		"Vector3":
			var c3 := _vector_components(data, 3, "Vector3")
			if not bool(c3.get("ok", false)): return c3
			var v3c: Array = c3["value"]
			return _ok(Vector3(v3c[0], v3c[1], v3c[2]))
		"Vector3i":
			var ci3 := _vector_components(data, 3, "Vector3i")
			if not bool(ci3.get("ok", false)): return ci3
			var v3i_c: Array = ci3["value"]
			return _ok(Vector3i(int(round(v3i_c[0])), int(round(v3i_c[1])), int(round(v3i_c[2]))))
		"Vector4":
			var c4 := _vector_components(data, 4, "Vector4")
			if not bool(c4.get("ok", false)): return c4
			var v4c: Array = c4["value"]
			return _ok(Vector4(v4c[0], v4c[1], v4c[2], v4c[3]))
		"Color":
			return _decode_color(data)
		"Rect2":
			return _decode_rect(data, false)
		"Rect2i":
			return _decode_rect(data, true)
		"Transform2D":
			var axis_x := _nested(data, "x", TYPE_VECTOR2)
			if not bool(axis_x.get("ok", false)):
				return axis_x
			var axis_y := _nested(data, "y", TYPE_VECTOR2)
			if not bool(axis_y.get("ok", false)):
				return axis_y
			var origin2 := _nested(data, "origin", TYPE_VECTOR2)
			if not bool(origin2.get("ok", false)):
				return origin2
			return _ok(Transform2D(axis_x["value"], axis_y["value"], origin2["value"]))
		"Transform3D":
			var basis_res := _nested(data, "basis", TYPE_BASIS)
			if not bool(basis_res.get("ok", false)):
				return basis_res
			var origin3 := _nested(data, "origin", TYPE_VECTOR3)
			if not bool(origin3.get("ok", false)):
				return origin3
			return _ok(Transform3D(basis_res["value"], origin3["value"]))
		"Basis":
			var row_x := _vector_components(data.get("x", null), 3, "Basis.x")
			if not bool(row_x.get("ok", false)): return row_x
			var row_y := _vector_components(data.get("y", null), 3, "Basis.y")
			if not bool(row_y.get("ok", false)): return row_y
			var row_z := _vector_components(data.get("z", null), 3, "Basis.z")
			if not bool(row_z.get("ok", false)): return row_z
			var xv: Array = row_x["value"]
			var yv: Array = row_y["value"]
			var zv: Array = row_z["value"]
			return _ok(Basis(Vector3(xv[0], xv[1], xv[2]), Vector3(yv[0], yv[1], yv[2]), Vector3(zv[0], zv[1], zv[2])))
		"Quaternion":
			var cq := _vector_components(data, 4, "Quaternion")
			if not bool(cq.get("ok", false)): return cq
			var qv: Array = cq["value"]
			return _ok(Quaternion(qv[0], qv[1], qv[2], qv[3]))
		"Plane":
			var normal := _nested(data, "normal", TYPE_VECTOR3)
			if not bool(normal.get("ok", false)):
				return normal
			return _ok(Plane(normal["value"], _to_float(data.get("d", 0.0), 0.0)))
		"AABB":
			var box_pos := _nested(data, "position", TYPE_VECTOR3)
			if not bool(box_pos.get("ok", false)):
				return box_pos
			var box_size := _nested(data, "size", TYPE_VECTOR3)
			if not bool(box_size.get("ok", false)):
				return box_size
			return _ok(AABB(box_pos["value"], box_size["value"]))
		"NodePath":
			return _ok(NodePath(String(data.get("path", ""))))
		"Object":
			return _decode_object(data, expected_type)
		"RID":
			return _error("RID values are read-only and cannot be decoded", {"tag": tag}, McpErrors.ERR_UNSUPPORTED)
		_:
			return _error("unknown __gd tag '%s'" % tag, {"tag": tag})


## Decodes Rect2 / Rect2i from position + size.
static func _decode_rect(data: Dictionary, integer: bool) -> Dictionary:
	var pos_type := TYPE_VECTOR2I if integer else TYPE_VECTOR2
	var size_type := TYPE_VECTOR2I if integer else TYPE_VECTOR2
	var rect_pos := _nested(data, "position", pos_type)
	if not bool(rect_pos.get("ok", false)):
		return rect_pos
	var rect_size := _nested(data, "size", size_type)
	if not bool(rect_size.get("ok", false)):
		return rect_size
	if integer:
		return _ok(Rect2i(rect_pos["value"], rect_size["value"]))
	return _ok(Rect2(rect_pos["value"], rect_size["value"]))


## Decodes {"__gd": "Object", "class", "path"} by loading the resource when
## possible; node references decode to null because the caller must resolve
## them by path through the node API.
static func _decode_object(data: Dictionary, expected_type: int) -> Dictionary:
	var path := String(data.get("path", ""))
	if path.is_empty():
		return _ok(null)
	if path.begins_with("res://") or path.begins_with("user://"):
		if not ResourceLoader.exists(path):
			return _error("resource not found: %s" % path, {"path": path}, McpErrors.ERR_RESOURCE_NOT_FOUND)
		var resource: Resource = ResourceLoader.load(path)
		if resource == null:
			return _error("could not load resource: %s" % path, {"path": path}, McpErrors.ERR_RESOURCE_NOT_FOUND)
		return _ok(resource)
	if expected_type == TYPE_NIL:
		# A node path is only meaningful to the node API; keep it as a string.
		return _ok(path)
	return _ok(null)


## Parses permissive string input (PROTOCOL.md §4).
static func _decode_string(text: String, expected_type: int) -> Dictionary:
	var raw := text.strip_edges()
	if expected_type == TYPE_STRING or expected_type == TYPE_STRING_NAME:
		return _ok(raw)
	if expected_type == TYPE_INT:
		return _string_to_int(raw)
	if expected_type == TYPE_FLOAT:
		return _string_to_float(raw)
	if expected_type == TYPE_BOOL:
		return _string_to_bool(raw)
	if expected_type == TYPE_NODE_PATH:
		return _ok(NodePath(raw))
	if expected_type == TYPE_COLOR:
		return _string_to_color(raw)
	if TYPE_NAMES.values().has(expected_type):
		return _string_to_typed(raw, expected_type)
	# TYPE_NIL: keep the string unless it is an unambiguous literal.
	if raw.begins_with("#") and raw.substr(1).is_valid_hex_number():
		var hex_color: Variant = _try_color(raw)
		if hex_color != null:
			return _ok(hex_color)
	var literal: Variant = _parse_constructor(raw)
	if literal != null:
		return _ok(literal)
	return _ok(text)


## Parses "Vector2(1, 2)" / "(1, 2)" / "1,2" into the expected type.
static func _string_to_typed(raw: String, expected_type: int) -> Dictionary:
	var body := raw
	var open := raw.find("(")
	if open > 0 and raw.ends_with(")"):
		body = raw.substr(open + 1, raw.length() - open - 2)
	elif raw.begins_with("(") and raw.ends_with(")"):
		body = raw.substr(1, raw.length() - 2)
	var numbers := _parse_numbers(body)
	if numbers.is_empty():
		return _error("cannot parse '%s' as %s" % [raw, type_string(expected_type)], {"value": raw})
	return _from_components(numbers, expected_type)


## Builds a typed value from a flat list of numbers.
##
## Each type declares the exact number of components it needs, so a short input
## can never be silently padded with zeros (a corrupt write is worse than an
## error the caller can fix).
static func _from_components(numbers: Array, expected_type: int) -> Dictionary:
	var required := _component_count(expected_type)
	if required > 0 and numbers.size() < required:
		return _error("%s needs %d numeric components, got %d" % [type_string(expected_type), required, numbers.size()],
			{"type": type_string(expected_type), "count": numbers.size()})
	var c := _pad(numbers, maxi(required, numbers.size()))
	match expected_type:
		TYPE_VECTOR2:
			return _ok(Vector2(c[0], c[1]))
		TYPE_VECTOR2I:
			return _ok(Vector2i(int(round(c[0])), int(round(c[1]))))
		TYPE_VECTOR3:
			return _ok(Vector3(c[0], c[1], c[2]))
		TYPE_VECTOR3I:
			return _ok(Vector3i(int(round(c[0])), int(round(c[1])), int(round(c[2]))))
		TYPE_VECTOR4:
			return _ok(Vector4(c[0], c[1], c[2], c[3]))
		TYPE_COLOR:
			var alpha: float = c[3] if numbers.size() >= 4 else 1.0
			return _ok(Color(c[0], c[1], c[2], alpha))
		TYPE_RECT2:
			return _ok(Rect2(Vector2(c[0], c[1]), Vector2(c[2], c[3])))
		TYPE_RECT2I:
			return _ok(Rect2i(Vector2i(int(round(c[0])), int(round(c[1]))), Vector2i(int(round(c[2])), int(round(c[3])))))
		TYPE_QUATERNION:
			return _ok(Quaternion(c[0], c[1], c[2], c[3]))
		TYPE_PLANE:
			return _ok(Plane(Vector3(c[0], c[1], c[2]), c[3]))
		TYPE_AABB:
			return _ok(AABB(Vector3(c[0], c[1], c[2]), Vector3(c[3], c[4], c[5])))
		_:
			return _error("cannot build %s from numeric components" % type_string(expected_type), {"count": numbers.size()})


## Parses explicit constructor-like strings such as "Vector2(1, 2)".
static func _parse_constructor(raw: String) -> Variant:
	var open := raw.find("(")
	if open <= 0 or not raw.ends_with(")"):
		return null
	var prefix := raw.substr(0, open).strip_edges()
	if not TYPE_NAMES.has(prefix):
		return null
	var body := raw.substr(open + 1, raw.length() - open - 2)
	var numbers := _parse_numbers(body)
	if numbers.is_empty():
		return null
	var built := _from_components(numbers, int(TYPE_NAMES[prefix]))
	if not bool(built.get("ok", false)):
		return null
	return built.get("value", null)


## Extracts every number from free-form text, keeping signs and decimals.
##
## Only tokens that really are numbers are kept: a stray "-", "+", "." or "e"
## (for example inside "garbage-value") must not turn into a silent 0.0.
static func _parse_numbers(text: String) -> Array:
	var out: Array = []
	var current := ""
	for index in range(text.length()):
		var ch := text.substr(index, 1)
		if _is_number_char(ch):
			current += ch
		else:
			_flush_number(current, out)
			current = ""
	_flush_number(current, out)
	return out


static func _is_number_char(ch: String) -> bool:
	return (ch >= "0" and ch <= "9") or ch == "." or ch == "-" or ch == "+" or ch == "e" or ch == "E"


## Appends `token` when it is a complete number; digit-less fragments are dropped.
static func _flush_number(token: String, out: Array) -> void:
	if token.is_empty():
		return
	var has_digit := false
	for index in range(token.length()):
		var ch := token.substr(index, 1)
		if ch >= "0" and ch <= "9":
			has_digit = true
			break
	if not has_digit:
		return
	if token.is_valid_float():
		out.append(token.to_float())
	elif token.is_valid_int():
		out.append(float(token.to_int()))


## Pads a numeric list with zeros so indexes 0..(size-1) always exist.
static func _pad(numbers: Array, size: int) -> Array:
	var out: Array = []
	for index in range(size):
		if index < numbers.size():
			out.append(_to_float(numbers[index], 0.0))
		else:
			out.append(0.0)
	return out


## Number of numeric components a type needs (0 = not component based).
static func _component_count(expected_type: int) -> int:
	match expected_type:
		TYPE_VECTOR2, TYPE_VECTOR2I:
			return 2
		TYPE_VECTOR3, TYPE_VECTOR3I, TYPE_COLOR:
			return 3
		TYPE_VECTOR4, TYPE_QUATERNION, TYPE_RECT2, TYPE_RECT2I, TYPE_PLANE:
			return 4
		TYPE_AABB:
			return 6
		_:
			return 0


## Reads r/g/b/a (preferred), then x/y/z/w, then a positional array.
##
## Missing alpha defaults to 1.0; a colour without three channels is a 1004
## failure instead of the silent Color(0,0,0,0) the old codec produced.
static func _decode_color(data: Dictionary) -> Dictionary:
	var flat: Array = []
	for key in ["r", "g", "b", "a"]:
		if data.has(key):
			flat.append(_to_float(data[key], 0.0))
	if flat.size() < 3:
		flat = []
		for key in ["x", "y", "z", "w"]:
			if data.has(key):
				flat.append(_to_float(data[key], 0.0))
	if flat.size() < 3:
		for key in ["value", "color", "components"]:
			if typeof(data.get(key, null)) == TYPE_ARRAY:
				flat = _numbers_of(data[key])
				break
	if flat.size() < 3:
		return _error("Color needs r, g, b (alpha optional) - got %d numeric channels" % flat.size(),
			{"type": "Color", "keys": data.keys()})
	var alpha: float = _to_float(flat[3], 1.0) if flat.size() >= 4 else 1.0
	return _ok(Color(flat[0], flat[1], flat[2], alpha))


## Flattens a dict/array/string/typed value into a flat list of numbers.
static func _numbers_of(source: Variant) -> Array:
	var flat: Array = []
	if typeof(source) == TYPE_DICTIONARY:
		var data: Dictionary = source
		for key in ["x", "y", "z", "w"]:
			if data.has(key):
				flat.append(_to_float(data[key], 0.0))
	elif typeof(source) == TYPE_ARRAY:
		var items: Array = source
		for item in items:
			flat.append(_to_float(item, 0.0))
	elif typeof(source) == TYPE_STRING:
		flat = _parse_numbers(String(source))
	else:
		flat = _from_variant(source)
	return flat


## Strict component reader: returns {"ok": true, "value": [...]} or a 1004 error.
static func _vector_components(source: Variant, count: int, label: String) -> Dictionary:
	var flat := _numbers_of(source)
	if flat.size() < count:
		return _error("%s needs %d numeric components, got %d" % [label, count, flat.size()],
			{"type": label, "count": flat.size()})
	var out: Array = []
	for index in range(count):
		out.append(_to_float(flat[index], 0.0))
	return _ok(out)


## Best-effort `__gd` tag suggestion used by codec error messages.
static func _tag_for(expected_type: int, raw: Dictionary) -> String:
	for name in TYPE_NAMES.keys():
		if int(TYPE_NAMES[name]) == expected_type:
			return String(name)
	if raw.has("r") or raw.has("g") or raw.has("b"):
		return "Color"
	if raw.has("x") or raw.has("y"):
		return "Vector2"
	return "Vector2"


## Flattens an already typed Godot value into numbers.
static func _from_variant(source: Variant) -> Array:
	match typeof(source):
		TYPE_VECTOR2:
			var v2: Vector2 = source
			return [v2.x, v2.y]
		TYPE_VECTOR2I:
			var v2i: Vector2i = source
			return [v2i.x, v2i.y]
		TYPE_VECTOR3:
			var v3: Vector3 = source
			return [v3.x, v3.y, v3.z]
		TYPE_VECTOR3I:
			var v3i: Vector3i = source
			return [v3i.x, v3i.y, v3i.z]
		TYPE_VECTOR4:
			var v4: Vector4 = source
			return [v4.x, v4.y, v4.z, v4.w]
		TYPE_COLOR:
			var color: Color = source
			return [color.r, color.g, color.b, color.a]
		TYPE_QUATERNION:
			var quat: Quaternion = source
			return [quat.x, quat.y, quat.z, quat.w]
		TYPE_INT, TYPE_FLOAT:
			return [_to_float(source, 0.0)]
		_:
			return []


## Decodes a nested sub-object into the requested type.
static func _nested(data: Dictionary, key: String, expected_type: int) -> Dictionary:
	if not data.has(key):
		return _error("missing '%s'" % key, {"key": key})
	return decode_checked(data[key], expected_type)


# ---------------------------------------------------------------------------
# Coercion
# ---------------------------------------------------------------------------

## Converts an already decoded value to `expected_type` when possible.
static func _coerce(value: Variant, expected_type: int) -> Dictionary:
	if expected_type == TYPE_NIL:
		return _ok(value)
	if expected_type == TYPE_DICTIONARY:
		if typeof(value) == TYPE_DICTIONARY:
			return _ok(value)
		return _error("expected an object, got %s" % type_string(typeof(value)), {})
	# A plain JSON object carries no type tag: it can never become a Vector2,
	# Color, Transform, NodePath, ... so fail loudly instead of zeroing it.
	if typeof(value) == TYPE_DICTIONARY:
		var raw: Dictionary = value
		if not raw.has("__gd"):
			return _error("plain objects are not accepted for %s - send {\"__gd\": \"%s\", ...}"
				% [type_string(expected_type), _tag_for(expected_type, raw)],
				{"type": type_string(expected_type), "keys": raw.keys()})
	# NodePath has no numeric components; it is coerced from strings below.
	if TYPE_NAMES.values().has(expected_type) and expected_type != TYPE_NODE_PATH:
		if typeof(value) == expected_type:
			return _ok(value)
		return _from_components(_from_variant(value), expected_type)
	match expected_type:
		TYPE_INT:
			return _to_int(value)
		TYPE_FLOAT:
			if typeof(value) in [TYPE_INT, TYPE_FLOAT, TYPE_BOOL]:
				return _ok(_to_float(value, 0.0))
			if typeof(value) == TYPE_STRING:
				return _string_to_float(String(value))
			return _error("expected a number, got %s" % type_string(typeof(value)), {})
		TYPE_BOOL:
			if typeof(value) == TYPE_STRING:
				return _string_to_bool(String(value))
			if typeof(value) == TYPE_NIL:
				return _ok(false)
			return _ok(bool(value))
		TYPE_STRING:
			if typeof(value) == TYPE_STRING:
				return _ok(value)
			if typeof(value) == TYPE_STRING_NAME:
				return _ok(String(value))
			if typeof(value) in [TYPE_INT, TYPE_FLOAT, TYPE_BOOL]:
				return _ok(str(value))
			return _error("cannot convert %s to String" % type_string(typeof(value)), {})
		TYPE_NODE_PATH:
			if typeof(value) == TYPE_NODE_PATH:
				return _ok(value)
			return _ok(NodePath(str(value)))
		TYPE_OBJECT:
			if value == null or value is Object:
				return _ok(value)
			return _error("expected an Object reference, got %s" % type_string(typeof(value)), {})
		TYPE_ARRAY:
			if typeof(value) == TYPE_ARRAY:
				return _ok(value)
			return _error("expected an array, got %s" % type_string(typeof(value)), {})
		TYPE_DICTIONARY:
			if typeof(value) == TYPE_DICTIONARY:
				return _ok(value)
			return _error("expected an object, got %s" % type_string(typeof(value)), {})
		TYPE_BASIS, TYPE_TRANSFORM2D, TYPE_TRANSFORM3D:
			if typeof(value) == expected_type:
				return _ok(value)
			return _error("cannot convert %s to %s" % [type_string(typeof(value)), type_string(expected_type)], {})
		_:
			if typeof(value) == expected_type:
				return _ok(value)
			return _error("cannot convert %s to %s" % [type_string(typeof(value)), type_string(expected_type)], {})


## Numeric integer conversion; floats are rounded (PROTOCOL.md §4).
static func _to_int(value: Variant) -> Dictionary:
	match typeof(value):
		TYPE_INT:
			return _ok(int(value))
		TYPE_FLOAT:
			return _ok(int(round(_to_float(value, 0.0))))
		TYPE_BOOL:
			return _ok(1 if bool(value) else 0)
		TYPE_STRING:
			return _string_to_int(String(value))
		_:
			return _error("expected an integer, got %s" % type_string(typeof(value)), {})


static func _string_to_int(raw: String) -> Dictionary:
	var text := raw.strip_edges()
	if text.is_valid_int():
		return _ok(text.to_int())
	if text.is_valid_float():
		return _ok(int(round(text.to_float())))
	return _error("'%s' is not an integer" % raw, {"value": raw})


static func _string_to_float(raw: String) -> Dictionary:
	var text := raw.strip_edges()
	if text.is_valid_float():
		return _ok(text.to_float())
	if text.is_valid_int():
		return _ok(float(text.to_int()))
	return _error("'%s' is not a number" % raw, {"value": raw})


static func _string_to_bool(raw: String) -> Dictionary:
	var text := raw.strip_edges().to_lower()
	if text in TRUE_WORDS:
		return _ok(true)
	if text in FALSE_WORDS:
		return _ok(false)
	return _error("'%s' is not a boolean" % raw, {"value": raw})


static func _string_to_color(raw: String) -> Dictionary:
	var direct: Variant = _try_color(raw)
	if direct != null:
		return _ok(direct)
	var numbers := _parse_numbers(raw)
	if numbers.size() >= 3:
		return _from_components(numbers, TYPE_COLOR)
	return _error("'%s' is not a colour" % raw, {"value": raw})


## Accepts "#rgb", "#rrggbb", "#rrggbbaa" and named colours.
static func _try_color(raw: String) -> Variant:
	var text := raw.strip_edges()
	if text.is_empty():
		return null
	if text.begins_with("#"):
		if not text.substr(1).is_valid_hex_number():
			return null
		return Color.html(text)
	var named := Color.from_string(text.to_lower(), Color(-1.0, -1.0, -1.0, -1.0))
	if named.r < 0.0:
		return null
	return named


## Tolerant float reader used for building typed values.
static func _to_float(value: Variant, fallback: float) -> float:
	match typeof(value):
		TYPE_FLOAT:
			return float(value)
		TYPE_INT:
			return float(int(value))
		TYPE_BOOL:
			return 1.0 if bool(value) else 0.0
		TYPE_STRING:
			var text := String(value).strip_edges()
			if text.is_valid_float():
				return text.to_float()
			return fallback
		_:
			return fallback


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------

static func _ok(value: Variant) -> Dictionary:
	return {"ok": true, "value": value}


static func _error(message: String, context: Dictionary = {}, code: int = ERR_CODEC) -> Dictionary:
	var failure := McpErrors.make(code, message, "Check the value type with godot_list_properties first.", context)
	return {
		"ok": false,
		"error": failure,
		"code": code,
		"message": message,
	}
