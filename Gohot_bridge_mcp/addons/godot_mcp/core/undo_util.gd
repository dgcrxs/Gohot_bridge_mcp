@tool
extends RefCounted

## McpUndo - one-action-per-RPC helper for EditorUndoRedoManager.
##
## PROTOCOL.md §3: every `W` (write) method MUST be wrapped in exactly one undo
## action so a single Ctrl+Z reverts the whole RPC call.
##
## Usage:
##   McpUndo.action(undo, "MCP: node.add")
##   ... perform mutations ...
##   McpUndo.commit(undo, true)


## Opens a new undo action. Must be followed by exactly one commit().
##
## Args:
##   undo: Editor undo manager (EditorPlugin.get_undo_redo()).
##   name: Action name shown in the editor history panel.
static func action(undo: EditorUndoRedoManager, name: String) -> void:
	if undo == null:
		push_error("[MCP] undo manager is null, cannot create action '%s'" % name)
		return
	undo.create_action(name)


## Closes the current undo action.
##
## Args:
##   undo: Editor undo manager.
##   execute: When true the registered operations are executed immediately.
static func commit(undo: EditorUndoRedoManager, execute: bool = true) -> void:
	if undo == null:
		push_error("[MCP] undo manager is null, cannot commit action")
		return
	undo.commit_action(execute)
