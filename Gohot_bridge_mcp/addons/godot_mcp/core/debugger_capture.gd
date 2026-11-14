@tool
extends EditorDebuggerPlugin

## Captures the running game's stdout/stderr (PROTOCOL.md §3.9 runtime.logs).
##
## The editor debugger forwards "print" and "error" messages from every game
## session to _capture(); the entries are kept in a ring buffer that runtime_ops
## merges with the game side autoload buffer.
##
## Never calls print() from _capture(): that would re-enter this same hook.

const MAX_ENTRIES := 2000
const DEFAULT_LIMIT := 200
## Session ids are assigned by the editor; 0 means "any session".
const ANY_SESSION := -1

var _entries: Array = []
var _seq := 0
var _running := false


## Tells the editor which debugger messages this plugin wants.
func _has_capture(prefix: String) -> bool:
	return prefix == "print" or prefix == "error"


## Receives one captured message from a game session.
##
## Args:
##   message: Message category ("print" or "error").
##   data: Message payload, data[0] is the text for both categories.
##   session_id: Editor debugger session that produced the message.
##
## Returns:
##   true when the message was consumed and must not reach the output panel.
func _capture(message: String, data: Array, session_id: int) -> bool:
	if session_id < ANY_SESSION:
		return false
	match message:
		"print":
			_append("info", _text_at(data, 0))
			return true
		"error":
			_append("error", _text_at(data, 0))
			return true
	return false


## Returns log entries newer than `since` (PROTOCOL.md §3.9 runtime.logs shape).
##
## Args:
##   since: Last seq the caller already has.
##   level: Optional level filter ("info", "warn", "error"); "" keeps all.
##   limit: Maximum entries returned; values <= 0 fall back to 200.
##
## Returns:
##   {"entries": [{seq, ts, level, text}], "next": int}
func get_logs(since: int = 0, level: String = "", limit: int = DEFAULT_LIMIT) -> Dictionary:
	var entries: Array = []
	for item in _entries:
		var record: Dictionary = item
		if int(record.get("seq", 0)) <= since:
			continue
		if not level.is_empty() and String(record.get("level", "")) != level:
			continue
		entries.append(record.duplicate(true))
	var bounded := limit if limit > 0 else DEFAULT_LIMIT
	if entries.size() > bounded:
		entries = entries.slice(entries.size() - bounded)
	return {"entries": entries, "next": _seq}


## Convenience accessor for callers that only need the newest entries.
func get_entries(limit: int = DEFAULT_LIMIT) -> Array:
	var since := _seq - (limit if limit > 0 else DEFAULT_LIMIT)
	var payload := get_logs(maxi(0, since), "", limit)
	var entries: Variant = payload.get("entries", [])
	return entries if typeof(entries) == TYPE_ARRAY else []


## Drops every captured entry. `seq` keeps increasing so `since` stays monotonic.
func clear() -> void:
	_entries.clear()


## Records whether a game session is believed to be running (dock panel hint).
func set_running(v: bool) -> void:
	_running = v


## True when the last reported game state was "running".
func is_running() -> bool:
	return _running


## Number of retained entries.
func size() -> int:
	return _entries.size()


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

## Appends one entry, keeping at most MAX_ENTRIES records.
func _append(level: String, text: String) -> void:
	if text.strip_edges().is_empty():
		return
	_seq += 1
	_entries.append({
		"seq": _seq,
		"ts": int(Time.get_unix_time_from_system()),
		"level": level,
		"text": text,
	})
	while _entries.size() > MAX_ENTRIES:
		_entries.pop_front()


## Reads data[index] as a String; non-string payloads fall back to str().
func _text_at(data: Array, index: int) -> String:
	if index < 0 or index >= data.size():
		return ""
	var value: Variant = data[index]
	if typeof(value) == TYPE_STRING:
		return String(value)
	if typeof(value) == TYPE_STRING_NAME:
		return String(value)
	if value == null:
		return ""
	return str(value)
