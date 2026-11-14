@tool
extends Control

## Scrolling log view used by the MCP dock panel.
##
## Built from code (no .tscn): a ScrollContainer holding one RichTextLabel.
## `append()` accepts both the transport log shape
## ({seq, ts, level, method, ok, ms, error}) and the runtime shape
## ({seq, ts, level, text}); lines are coloured by level and the method name is
## highlighted. Auto-scrolls to the bottom after every append.

const DEFAULT_MAX_LINES := 500
const MIN_MAX_LINES := 20
const MAX_MAX_LINES := 5000

const TEXT_COLOR := Color(0.82, 0.84, 0.88)
const ERROR_COLOR := Color(1.0, 0.45, 0.40)
const WARN_COLOR := Color(1.0, 0.80, 0.35)
const METHOD_COLOR := Color(0.55, 0.78, 1.0)
const STAMP_COLOR := Color(0.52, 0.54, 0.58)

var _scroll: ScrollContainer = null
var _text: RichTextLabel = null
var _max_lines := DEFAULT_MAX_LINES
## Bounded copy of the rendered entries, used to rebuild after trimming.
var _entries: Array = []


func _init() -> void:
	custom_minimum_size = Vector2(0, 160)
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	_scroll = ScrollContainer.new()
	_scroll.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	add_child(_scroll)
	_text = RichTextLabel.new()
	_text.bbcode_enabled = false
	_text.fit_content = true
	_text.scroll_active = false
	_text.selection_enabled = true
	_text.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_scroll.add_child(_text)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

## Appends one log entry and keeps the view scrolled to the newest line.
func append(entry: Dictionary) -> void:
	if _text == null:
		return
	var normalised := _normalise(entry)
	if normalised.is_empty():
		return
	_entries.append(normalised)
	_write_line(normalised)
	while _entries.size() > _max_lines:
		_entries.pop_front()
		if _text.has_method("remove_paragraph"):
			_text.remove_paragraph(0)
		else:
			_rebuild()
			break
	_scroll_to_bottom()


## Drops every rendered line.
func clear() -> void:
	_entries.clear()
	if _text != null:
		_text.clear()
	if _scroll != null:
		_scroll.scroll_vertical = 0


## Caps the number of retained lines (default 500).
func set_max_lines(lines: int) -> void:
	_max_lines = clampi(lines, MIN_MAX_LINES, MAX_MAX_LINES)
	while _entries.size() > _max_lines:
		_entries.pop_front()
	_rebuild()


## Number of retained lines.
func line_count() -> int:
	return _entries.size()


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

## Converts either log shape into {ts, level, method, text}.
##
## Transport entries carry `method`/`ms`/`error`, runtime entries carry `text`;
## the method is rendered separately so it can be highlighted.
func _normalise(entry: Dictionary) -> Dictionary:
	var level := String(entry.get("level", "info"))
	var method := String(entry.get("method", ""))
	var body := String(entry.get("text", "")).strip_edges()
	var failure := String(entry.get("error", "")).strip_edges()
	var milliseconds := int(entry.get("ms", 0))
	if body.is_empty():
		var parts: Array = []
		if milliseconds > 0:
			parts.append("%d ms" % milliseconds)
		if not failure.is_empty():
			parts.append(failure)
		body = "  ".join(parts)
	if body.is_empty() and method.is_empty():
		return {}
	return {
		"ts": int(entry.get("ts", 0)),
		"level": level,
		"method": method,
		"text": body,
	}


## Writes one coloured line into the RichTextLabel.
func _write_line(entry: Dictionary) -> void:
	var level := String(entry.get("level", "info"))
	var colour := _colour_for(level)
	var method := String(entry.get("method", ""))
	var body := String(entry.get("text", ""))
	_text.push_color(STAMP_COLOR)
	_text.add_text(_stamp(int(entry.get("ts", 0))) + "  ")
	_text.pop()
	if not method.is_empty():
		_text.push_color(METHOD_COLOR)
		_text.add_text(method + "  ")
		_text.pop()
	_text.push_color(colour)
	_text.add_text(body)
	_text.pop()
	_text.newline()


## Re-renders every retained entry (used after trimming or a resize).
func _rebuild() -> void:
	if _text == null:
		return
	_text.clear()
	for item in _entries:
		_write_line(item)


## Keeps the scroll container pinned to the newest line.
func _scroll_to_bottom() -> void:
	call_deferred("_apply_scroll_bottom")


func _apply_scroll_bottom() -> void:
	if _scroll == null or not is_instance_valid(_scroll):
		return
	var bar := _scroll.get_v_scroll_bar()
	if bar == null:
		return
	_scroll.scroll_vertical = int(bar.max_value)


## Colour for one protocol log level.
func _colour_for(level: String) -> Color:
	match level:
		"error", "fatal":
			return ERROR_COLOR
		"warn", "warning":
			return WARN_COLOR
		_:
			return TEXT_COLOR


## "HH:MM:SS" stamp for a unix timestamp, or a placeholder when unknown.
func _stamp(unix_time: int) -> String:
	if unix_time <= 0:
		return "--:--:--"
	var time_string := Time.get_time_string_from_unix_time(unix_time)
	if time_string.is_empty():
		return "--:--:--"
	return time_string
