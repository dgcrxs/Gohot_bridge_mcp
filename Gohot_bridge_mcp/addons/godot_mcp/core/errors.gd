@tool
extends RefCounted

## McpErrors - error factory for the Godot Bridge MCP protocol (v2.0).
##
## Loaded through `const McpErrors = preload("res://addons/godot_mcp/core/errors.gd")`.
## Intentionally declares no `class_name` so the addon never pollutes the global
## script namespace of the host project.
##
## The error payload shape is fixed by PROTOCOL.md §2:
##   {"code": int, "message": String, "hint": String, "context": Dictionary}
## Tool-side helpers receive the wrapper {"__error": true, "error": {...}} so a
## business result (a Dictionary) can never be confused with a failure.

const ERR_PARSE := -32700
const ERR_INVALID_REQUEST := -32600
const ERR_METHOD_NOT_FOUND := -32601
const ERR_INVALID_PARAMS := -32602
const ERR_INTERNAL := -32603

const ERR_NO_SCENE := 1001
const ERR_NODE_NOT_FOUND := 1002
const ERR_PROPERTY_NOT_FOUND := 1003
const ERR_TYPE_MISMATCH := 1004
const ERR_PATH_OUTSIDE := 1005
const ERR_RESOURCE_NOT_FOUND := 1006
const ERR_SCRIPT_COMPILE := 1007
const ERR_RUNTIME_NOT_RUNNING := 1008
const ERR_UNDO_FAILED := 1009
const ERR_TIMEOUT := 1010
const ERR_AUTH_FAILED := 1011
const ERR_PERMISSION_DENIED := 1012
const ERR_FILE_EXISTS := 1013
const ERR_UNSUPPORTED := 1014

## PROTOCOL.md §2: `trace` is optional and capped at 8 entries.
const MAX_TRACE := 8


## Builds the canonical error wrapper.
##
## Args:
##   code: One of the numeric codes above.
##   message: Short, human readable description (no host-language stack traces).
##   hint: Actionable next step for the caller.
##   context: Small dictionary of relevant request fields.
##
## Returns:
##   {"__error": true, "error": {"code", "message", "hint", "context"}}
static func make(code: int, message: String, hint: String = "", context: Dictionary = {}) -> Dictionary:
	var error: Dictionary = {
		"code": code,
		"message": message,
		"hint": hint,
		"context": context,
	}
	return {"__error": true, "error": error}


## Alias of make(); kept because ops code reads better as `return fail(...)`.
static func fail(code: int, message: String, hint: String = "", context: Dictionary = {}) -> Dictionary:
	return make(code, message, hint, context)


## Appends one trace frame, keeping at most MAX_TRACE entries.
##
## Args:
##   error: The dictionary returned by make()/fail().
##   frame: Short location marker such as "editor_ops.gd:212".
##
## Returns:
##   The same dictionary, for chaining.
static func with_trace(error: Dictionary, frame: String) -> Dictionary:
	var payload: Variant = error.get("error", null)
	if typeof(payload) != TYPE_DICTIONARY:
		return error
	var inner: Dictionary = payload
	var trace: Array = []
	if inner.has("trace") and typeof(inner["trace"]) == TYPE_ARRAY:
		trace = (inner["trace"] as Array).duplicate()
	trace.append(frame)
	while trace.size() > MAX_TRACE:
		trace.pop_front()
	inner["trace"] = trace
	error["error"] = inner
	return error


## True when `value` carries the {"__error": true} marker.
static func is_error(value: Variant) -> bool:
	if typeof(value) != TYPE_DICTIONARY:
		return false
	var data: Dictionary = value
	if bool(data.get("__error", false)):
		return true
	# Tolerate a bare {"error": {"code": ...}} envelope coming from a module.
	if not data.has("error"):
		return false
	var inner: Variant = data["error"]
	if typeof(inner) != TYPE_DICTIONARY:
		return false
	return (inner as Dictionary).has("code")


## Extracts the inner error payload, even from a nested wrapper.
##
## Returns:
##   The {"code","message","hint","context"} dictionary, or an empty dictionary.
static func payload(value: Variant) -> Dictionary:
	if not is_error(value):
		return {}
	var data: Dictionary = value
	var inner: Variant = data.get("error", {})
	if typeof(inner) != TYPE_DICTIONARY:
		return {}
	var error: Dictionary = inner
	# Unwrap one accidental level of nesting.
	if error.has("__error") and typeof(error.get("error", null)) == TYPE_DICTIONARY:
		error = error["error"]
	if not error.has("context"):
		error["context"] = {}
	if not error.has("hint"):
		error["hint"] = ""
	return error


## Numeric code of an error value, or 0 when the value is not an error.
static func code_of(value: Variant) -> int:
	var error := payload(value)
	if error.is_empty():
		return 0
	return int(error.get("code", 0))


## Human readable message of an error value, or "" when not an error.
static func message_of(value: Variant) -> String:
	var error := payload(value)
	if error.is_empty():
		return ""
	return String(error.get("message", ""))
