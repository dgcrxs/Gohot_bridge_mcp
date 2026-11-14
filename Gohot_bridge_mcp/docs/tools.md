# 工具参考 / Tool Reference

共 **74** 个工具。所有工具返回 dict；失败时返回
`{"ok": false, "error": {"code", "message", "hint", "context"}}`，
错误码见 [PROTOCOL.md](../PROTOCOL.md#5-error-codes)。

> 本文件由 `scripts/gen_tools_doc.py` 从服务端定义自动生成，请勿手工编辑。

## 系统 / System

| 工具 | 说明 | 参数 |
|---|---|---|
| `godot_get_capabilities` | List the RPC methods supported by the connected Godot plugin. | — |
| `godot_get_config` | Show editor bridge settings (port, token masked, auto start) and local config. | — |
| `godot_get_project_settings` | Read project settings, optionally filtered by key prefix. | `prefix?`: string |
| `godot_get_selection` | Return the nodes currently selected in the editor scene dock. | — |
| `godot_get_undo_history` | List the editor undo stack of the current scene, newest first. At most 50 actions are returned. `mergeable` is always false: UndoRedo does not expose the merge flag of a committed action. | — |
| `godot_list_methods` | List every RPC method name the connected plugin exposes. | — |
| `godot_ping` | Ping the Godot editor bridge and measure round-trip health. | — |
| `godot_redo` | Redo previously undone editor action(s). | `steps?`: integer = `1` |
| `godot_server_info` | Report MCP server version, SDK flavour, bridge target and editor info. Use this first when debugging: it never fails, it degrades per section. | — |
| `godot_set_project_setting` | Change one project setting and save `project.godot`. | `key`: string<br>`value`: any |
| `godot_set_selection` | Select nodes in the editor scene dock. | `paths`: string[]<br>`inspect?`: boolean = `False` |
| `godot_undo` | Undo the last editor action(s). | `steps?`: integer = `1` |

## 场景 / Scene

| 工具 | 说明 | 参数 |
|---|---|---|
| `godot_create_scene` | Create a new scene file with a single root node and open it. | `path`: string<br>`root_type?`: string = `Node2D`<br>`root_name?`: string = `Root` |
| `godot_get_current_scene` | Describe the scene that is currently open in the editor. | — |
| `godot_get_scene_tree` | Return the node tree of the currently edited scene. | `depth?`: integer = `-1`<br>`include_internal?`: boolean = `False`<br>`with_props?`: boolean = `False` |
| `godot_list_open_scenes` | List all scenes open in the editor, marking the active one. | — |
| `godot_open_scene` | Open a scene file in the editor. | `path`: string |
| `godot_save_scene` | Save the current scene, optionally to a different path. | `path?`: any |
| `godot_save_scene_as` | Save the current scene under a new path (Save As). | `path`: string |

## 节点 / Node

| 工具 | 说明 | 参数 |
|---|---|---|
| `godot_add_node` | Add a new node under a parent and return its path. | `parent_path`: string<br>`type`: string<br>`name?`: string<br>`index?`: integer = `-1`<br>`properties?`: any |
| `godot_call_node_method` | Call a method on a node inside the editor. | `path`: string<br>`method`: string<br>`args?`: any |
| `godot_delete_node` | Delete a node and its children. Reversible with `godot_undo`. | `path`: string |
| `godot_duplicate_node` | Duplicate a node (and children) under the same parent. | `path`: string<br>`name?`: string |
| `godot_find_nodes` | Search the edited scene for nodes by name pattern, type, group or script. All filters are combined with AND; an empty filter matches everything. | `pattern?`: string<br>`type?`: string<br>`group?`: string<br>`script?`: string<br>`root_path?`: string<br>`limit?`: integer = `200` |
| `godot_get_node` | Read one node: type, owner, script, groups, child count and optional properties. | `path`: string<br>`with_props?`: boolean = `False` |
| `godot_move_node` | Re-parent a node, optionally at a specific sibling index. | `path`: string<br>`new_parent`: string<br>`index?`: integer = `-1` |
| `godot_rename_node` | Rename a node, keeping its position in the tree. | `path`: string<br>`new_name`: string |

## 属性 / Property

| 工具 | 说明 | 参数 |
|---|---|---|
| `godot_get_properties` | Read all matching properties of a node as a flat name -> value map. | `path`: string<br>`prefix?`: string |
| `godot_get_property` | Read a single property value, encoded with the value codec. | `path`: string<br>`property`: string |
| `godot_list_properties` | List every inspector property of a node with its type and hint. | `path`: string<br>`with_values?`: boolean = `False` |
| `godot_reset_property` | Restore a property to the class default value. | `path`: string<br>`property`: string |
| `godot_set_properties` | Set several properties in one undoable step. | `path`: string<br>`values`: object |
| `godot_set_property` | Set one property and return the previous and new values. Godot types accept the permissive codec: `{"__gd":"Vector2","x":1,"y":2}`, `"1,2"`, `[1,2]`, `"#ff0000"`, `"red"` or plain numbers. | `path`: string<br>`property`: string<br>`value`: any |

## 信号 / Signal

| 工具 | 说明 | 参数 |
|---|---|---|
| `godot_connect_signal` | Connect a node signal to a method on another node in one undo step. | `path`: string<br>`signal`: string<br>`target_path`: string<br>`method`: string<br>`flags?`: any |
| `godot_disconnect_signal` | Remove one signal connection in one undo step. | `path`: string<br>`signal`: string<br>`target_path`: string<br>`method`: string |
| `godot_list_signals` | List every signal of a node with its arguments and live connections. | `path`: string<br>`include_internal?`: boolean = `False` |

## 脚本 / Script

| 工具 | 说明 | 参数 |
|---|---|---|
| `godot_attach_script` | Attach a script to a node in the edited scene. | `node_path`: string<br>`script_path`: string |
| `godot_check_script` | Compile-check GDScript source or an existing script file. | `source?`: string<br>`path?`: string |
| `godot_create_script` | Create a new `.gd` file, optionally with generated boilerplate. | `path`: string<br>`source?`: string<br>`extends?`: string = `Node` |
| `godot_delete_script` | Delete a script file from the project. | `path`: string |
| `godot_detach_script` | Remove the script from a node. | `node_path`: string |
| `godot_execute_script` | Run a GDScript snippet inside the edited scene and return its value. The snippet body must `return` a value; it runs on a temporary node under the edited scene root, which is removed afterwards. | `code`: string |
| `godot_list_scripts` | List `.gd` files in the project with their class name and base class. | `dir?`: string = `res://`<br>`recursive?`: boolean = `True` |
| `godot_read_script` | Read a `.gd` script file as UTF-8 text. | `path`: string |
| `godot_run_gdscript_in_game` | Evaluate a GDScript snippet inside the running game process. | `code`: string |
| `godot_write_script` | Overwrite a script file, preserving its newline style. | `path`: string<br>`source`: string |

## 资源与文件 / Resource & File

| 工具 | 说明 | 参数 |
|---|---|---|
| `godot_create_resource` | Create and save a new resource file. | `path`: string<br>`type`: string<br>`properties?`: any |
| `godot_delete_resource` | Delete a resource file from the project. | `path`: string |
| `godot_get_resource_dependencies` | List the resources a scene or resource depends on. | `path`: string |
| `godot_get_resource_info` | Describe one resource: type, resource path, uid and import state. | `path`: string |
| `godot_list_directory` | List directory entries without loading resources. | `path?`: string = `res://`<br>`show_hidden?`: boolean = `False` |
| `godot_list_resources` | List resource files in a project directory. | `dir?`: string = `res://`<br>`type?`: string<br>`recursive?`: boolean = `True`<br>`limit?`: integer = `500` |
| `godot_load_resource` | Load a resource and return its type plus serialized properties. | `path`: string |
| `godot_read_file` | Read any project file; binary content comes back base64 encoded. | `path`: string<br>`max_bytes?`: integer = `1048576` |
| `godot_reimport_files` | Trigger a filesystem rescan / asset reimport. | `path?`: string |
| `godot_search_files` | Grep project files for a text query. | `query`: string<br>`dir?`: string = `res://`<br>`glob?`: string = `*.gd;*.tscn;*.tres;*.res;*.json;*.cfg;*.txt;*.md`<br>`limit?`: integer = `100` |
| `godot_write_file` | Write a project file (text, or base64 when `encoding` is `"base64"`). | `path`: string<br>`content`: string<br>`encoding?`: string = `utf-8` |

## 运行时试玩 / Runtime

| 工具 | 说明 | 参数 |
|---|---|---|
| `godot_get_game_logs` | Read log lines printed by the running game. | `since?`: integer = `0`<br>`level?`: string<br>`limit?`: integer = `200` |
| `godot_get_runtime_node` | Inspect a node inside the running game. | `path`: string<br>`with_props?`: boolean = `False` |
| `godot_get_runtime_status` | Report whether the game is running and which scene it plays. | — |
| `godot_get_runtime_tree` | Return the live scene tree of the running game. | `depth?`: integer = `-1` |
| `godot_run_scene` | Start the game (Play) in the running game process. | `scene?`: string |
| `godot_send_input` | Inject synthetic input events into the running game, in order. Supported event shapes: `{"type": "key", "keycode": "SPACE", "pressed": true}` `{"type": "key", "keycode": "A", "pressed": true, "shift": true}` `{"type": "mouse", "x": 320, "y": 180, "button": "left", "pressed": true}` `{"type": "motion", "x": 320, "y": 180}` `{"type": "action", "action": "jump", "pressed": true, "strength": 1.0}` `{"type": "wait", "ms": 250}` | `events`: object[] |
| `godot_stop_scene` | Stop the running game. | — |
| `godot_take_screenshot` | Capture the running game viewport as a base64 PNG. | `max_size?`: integer = `1280` |

## 类文档 / Class docs

| 工具 | 说明 | 参数 |
|---|---|---|
| `godot_get_class_info` | Describe a Godot class: base class, methods, properties, signals, enums. | `class_name`: string |
| `godot_search_classes` | Search Godot classes by name, to pick a valid node type. | `query`: string<br>`limit?`: integer = `50`<br>`instantiable_only?`: boolean = `False` |

## v1 兼容工具 / Legacy

| 工具 | 说明 | 参数 |
|---|---|---|
| `add_node` | Add a node to the edited scene (legacy v1 tool). | `node_type`: string<br>`node_name?`: string<br>`parent_path?`: string |
| `execute_script` | Run GDScript in the editor (legacy v1 tool). | `code`: string |
| `get_editor_info` | Return editor metadata (legacy v1 tool). | — |
| `get_node_properties` | Return all properties of a node (legacy v1 tool). | `node_path`: string |
| `get_scene_tree` | Return the node tree of the edited scene (legacy v1 tool). | — |
| `get_selected_nodes` | Return the nodes selected in the editor (legacy v1 tool). | — |
| `set_node_property` | Set one node property (legacy v1 tool). | `node_path`: string<br>`property`: string<br>`value`: any |

## 注解统计

- 只读工具：0 / 74
- 写工具（会创建 undo action）：74
