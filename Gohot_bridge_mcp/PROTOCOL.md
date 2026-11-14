# Godot Bridge MCP — Protocol Contract v2.0

This file is the single source of truth for the Godot plugin (`addons/godot_mcp`) and the
Python server (`server/`). Both sides MUST follow it exactly.

## 1. Transport

- WebSocket, text frames, UTF-8 JSON. Bind `127.0.0.1` only.
- Editor port: default `8765`, auto-increment up to `+19` when busy.
- Runtime port (game process): `editor_port + 1`, auto-increment when busy.
- Each frame is one JSON object. No batching at transport level.
- Heartbeat: editor sends WebSocket ping every 5 s; a client idle for 30 s is closed.

### Handshake file

Written by the Godot plugin on start to `<project>/.godot/mcp_bridge.json`:

```json
{
  "schema": 1,
  "protocol": "2.0",
  "port": 8765,
  "runtime_port": 8766,
  "token": "48-hex-chars",
  "pid": 12345,
  "project_path": "/abs/path/to/project",
  "project_name": "MyGame",
  "godot_version": "4.7.2",
  "started_at": 1763100000
}
```

Python discovery order: explicit args → env (`GODOT_MCP_PORT`, `GODOT_MCP_TOKEN`,
`GODOT_MCP_HANDSHAKE`, `GODOT_MCP_PROJECT`) → walk up from cwd looking for
`.godot/mcp_bridge.json` → scan ports `8765..8784` with an unauthenticated `ping`.

## 2. Envelope

Request:

```json
{"jsonrpc":"2.0","id":"c1-7","method":"prop.set","params":{"path":"Player","property":"modulate","value":{"__gd":"Color","r":1,"g":0,"b":0,"a":1}}}
```

Success:

```json
{"jsonrpc":"2.0","id":"c1-7","result":{"old":{...},"new":{...}},"elapsed_ms":3}
```

Failure:

```json
{"jsonrpc":"2.0","id":"c1-7","error":{"code":1003,"message":"property 'modulate' not found","hint":"Call godot_list_properties first.","context":{"path":"Player","property":"modulate"},"trace":["editor_ops.gd:212"]}}
```

Rules:

- `id` is a string, echoed verbatim. Requests without `jsonrpc` are treated as legacy v1.
- `params` always an object, never null.
- `error.code` MUST be one of the codes in §7.
- `trace` optional, max 8 entries, never a raw stack dump of the host language.

### Auth

First frame on every connection:

```json
{"jsonrpc":"2.0","id":"0","method":"auth","params":{"token":"<token>"}}
```

Response `{"result":{"ok":true,"client_id":"c1"}}`. Wrong token → `error.code=1011`
then close with code `4001`. Only `ping` and `auth` are allowed before auth.
If the configured token is empty, auth is skipped.

## 3. Method groups

`W` = write operation; the Godot side MUST wrap it in exactly one
`EditorUndoRedoManager` action so one `Ctrl+Z` reverts the whole call.
Results listed are the `result` payload (not the envelope).

### 3.1 System

| Method | Params | Result |
|---|---|---|
| `ping` | — | `{pong, protocol, project, pid, requires_auth}` |
| `auth` | `{token}` | `{ok, client_id}` |
| `get_capabilities` | — | `{protocol, plugin_version, godot_version, methods:[string], groups:[string]}` |
| `get_editor_info` | — | `{godot_version, system, project_name, project_path, scene_path, scene_modified, open_scenes:[{path,current}], selection_count, is_playing, locale}` |
| `get_config` | — | `{port, runtime_port, token_masked, auto_start, has_runtime_bridge}` |
| `set_config` W | `{auto_start?, port?}` | `{port, auto_start, restart_required}` |
| `log.list` | `{since?, level?, limit?}` | `{entries:[{seq,ts,level,method,ok,ms,error}], next}` |
| `log.clear` W | — | `{cleared}` |

### 3.2 Scene

| Method | Params | Result |
|---|---|---|
| `scene.get_tree` | `{depth?, include_internal?, with_props?}` | `{root:{name,type,path,script,owner,children:[]}, count}` |
| `scene.current` | — | `{path, modified, root_name, root_type}` |
| `scene.list_open` | — | `{scenes:[{path,current,modified}]}` |
| `scene.open` W | `{path}` | `{path, root_type}` |
| `scene.save` W | `{path?}` | `{saved, path}` |
| `scene.save_as` W | `{path}` | `{saved, path}` |
| `scene.create` W | `{path, root_type?, root_name?}` | `{path, root_type}` |

### 3.3 Node

| Method | Params | Result |
|---|---|---|
| `node.get` | `{path, with_props?}` | `{name,type,path,owner,script,scene_file,groups,children_count,properties?}` |
| `node.add` W | `{parent_path, type, name?, index?, properties?}` | `{path, name, type}` |
| `node.remove` W | `{path}` | `{removed, path}` |
| `node.rename` W | `{path, name}` | `{path, old_name}` |
| `node.move` W | `{path, new_parent, index?}` | `{path, parent}` |
| `node.duplicate` W | `{path, name?}` | `{path}` |
| `node.find` | `{pattern?, type?, group?, script?, root_path?, limit?}` | `{nodes:[{path,name,type}], count}` |
| `node.call` W | `{path, method, args?}` | `{result}` |

Path convention: `""` or `"."` = scene root; `"Player"` = relative to root;
`"/root/Player"` = absolute (also accepted). Never expose `@` internal nodes unless
`include_internal` is true.

### 3.4 Property

| Method | Params | Result |
|---|---|---|
| `prop.list` | `{path, include_internal?, with_values?}` | `{properties:[{name,type,hint,usage,value?}]}` |
| `prop.get` | `{path, property}` | `{value}` |
| `prop.get_all` | `{path, prefix?}` | `{properties:{name:value}}` |
| `prop.set` W | `{path, property, value}` | `{old, new}` |
| `prop.set_many` W | `{path, values:{}}` | `{applied, old:{}}` |
| `prop.reset` W | `{path, property}` | `{value}` |

### 3.5 Signal

| Method | Params | Result |
|---|---|---|
| `signal.list` | `{path, include_internal?}` | `{signals:[{name,args:[{name,type}],connections:[{target,method,flags}]}]}` — internal (`@`) connections are hidden unless `include_internal=true` |
| `signal.connect` W | `{path, signal, target_path, method, flags?}` | `{connected}` |
| `signal.disconnect` W | `{path, signal, target_path, method}` | `{disconnected}` |

### 3.6 Script

| Method | Params | Result |
|---|---|---|
| `script.read` | `{path}` | `{path, source, lines}` |
| `script.write` W | `{path, source}` | `{path, bytes}` |
| `script.create` W | `{path, source?, extends?}` | `{path}` |
| `script.delete` W | `{path}` | `{deleted, path}` |
| `script.attach` W | `{node_path, script_path}` | `{attached, path}` |
| `script.detach` W | `{node_path}` | `{detached}` |
| `script.check` | `{source?, path?}` | `{ok, errors:[{line,message}]}` |
| `script.list` | `{dir?, recursive?}` | `{scripts:[{path,class_name,extends}]}` |
| `script.search` | `{query, dir?, glob?, limit?}` | `{matches:[{path,line,text}], count}` |

`script.write` must preserve the file's existing newline style and BOM-free UTF-8.

### 3.7 Resource / FileSystem

| Method | Params | Result |
|---|---|---|
| `res.list` | `{dir, type?, recursive?, limit?}` | `{entries:[{path,type,size}], count}` |
| `res.info` | `{path}` | `{path,type,resource_path,uid,imported}` |
| `res.load` | `{path}` | `{type, properties}` |
| `res.create` W | `{path, type, properties?}` | `{path, type}` |
| `res.duplicate` W | `{src, dst}` | `{path}` |
| `res.delete` W | `{path}` | `{deleted, path}` |
| `res.dependencies` | `{path}` | `{dependencies:[{path,type}]}` |
| `res.reimport` W | `{path?}` | `{scanned, files}` |
| `file.read` | `{path, max_bytes?}` | `{path, content, encoding:"utf-8"/"base64", size}` |
| `file.write` W | `{path, content, encoding?}` | `{path, bytes}` |
| `fs.search` | `{query, dir?, glob?, limit?}` | `{matches:[{path,line,text}], count}` |
| `fs.list_dir` | `{path, show_hidden?}` | `{entries:[{name,path,dir,size}]}` |

All `res://`/`user://` paths are allowed; raw OS paths only if they resolve inside the
project. Anything else → `1005`.

### 3.8 Editor

| Method | Params | Result |
|---|---|---|
| `editor.get_selection` | — | `{nodes:[{path,name,type}]}` |
| `editor.set_selection` W | `{paths:[], inspect?}` | `{count}` |
| `editor.undo` W | `{steps?}` | `{done}` |
| `editor.redo` W | `{steps?}` | `{done}` |
| `editor.undo_history` | — | `{actions:[{name,mergeable}]}` — newest first, capped at 50 entries |
| `editor.inspect` W | `{path}` | `{inspected}` |
| `editor.open_script` W | `{path, line?}` | `{opened}` |
| `editor.project_settings` | `{prefix?}` | `{settings:{}}` |
| `editor.set_project_setting` W | `{key, value}` | `{key, old}` |
| `editor.autoloads` | — | `{autoloads:[{name,path,enabled}]}` |

### 3.9 Runtime (game process)

| Method | Params | Result |
|---|---|---|
| `runtime.status` | — | `{running, connected, scene, pid?}` |
| `runtime.run` W | `{scene?, args?}` | `{running, scene}` |
| `runtime.stop` W | — | `{running:false}` |
| `runtime.logs` | `{since?, level?, limit?}` | `{entries:[{seq,ts,level,text}], next, running}` |
| `runtime.screenshot` | `{max_size?}` | `{png_base64, width, height}` |
| `runtime.input` | `{events:[...]}` | `{sent}` |
| `runtime.eval` W | `{code}` | `{result}` |
| `runtime.get_tree` | `{depth?}` | `{root:{...}}` |
| `runtime.get_node` | `{path, with_props?}` | `{name,type,path,properties?}` |

`runtime.input` events, executed in order with awaited delays:

```json
{"type":"key","keycode":"SPACE","pressed":true}
{"type":"key","keycode":"A","pressed":true,"shift":true}
{"type":"mouse","x":320,"y":180,"button":"left","pressed":true}
{"type":"motion","x":320,"y":180}
{"type":"action","action":"jump","pressed":true,"strength":1.0}
{"type":"wait","ms":250}
```

`runtime.screenshot` returns base64 of a PNG, downscaled so `max(width,height) <= max_size`
(default 1280). If the game is not running → `1008`.

### 3.10 Docs / Introspection

| Method | Params | Result |
|---|---|---|
| `docs.class` | `{name}` | `{name, inherits, instantiable, methods:[{name,return_type,args}], properties:[{name,type}], signals:[{name,args}], enums:[{name,values}]}` |
| `docs.search` | `{query, limit?, instantiable_only?}` | `{classes:[{name,inherits,instantiable}]}` |

### 3.11 Legacy v1 compatibility

| Method | Maps to |
|---|---|
| `get_scene_tree` | `scene.get_tree` |
| `add_node` | `node.add` (`type`, `name`, `parent_path`) |
| `get_node_properties` | `prop.get_all` (`path`) |
| `set_node_property` | `prop.set` (`path`,`property`,`value`) |
| `execute_script` | `execute.gdscript` |
| `get_selected_nodes` | `editor.get_selection` |
| `get_editor_info` | `get_editor_info` |
| `list_node_types` | `docs.search` with empty query |

### 3.12 Execute

| Method | Params | Result |
|---|---|---|
| `execute.gdscript` | `{code, base_path?, timeout_ms?}` | `{result, output, elapsed_ms}` |

The code is wrapped in a `Node` script that `extends Node` and defines
`func _mcp_run():`, inserted under the edited scene root, removed afterwards.
Multi-line code is indented correctly (one tab). The wrapper itself must be inserted
with a script created via `GDScript.new()` + `source_code` + `reload()`; if
`reload()` returns an error, return `1007` with the compiler message.
`result` is encoded with the value codec; `print()` output produced by the snippet is
captured by temporarily overriding nothing — only the returned value is reported.

## 4. Value codec

Encoded values are JSON-safe. Native JSON types (null/bool/int/float/string/array/dict)
pass through unchanged. Godot types become objects with a `__gd` key.

| Type | Encoding |
|---|---|
| Vector2 | `{"__gd":"Vector2","x":1,"y":2}` |
| Vector2i | `{"__gd":"Vector2i","x":1,"y":2}` |
| Vector3 | `{"__gd":"Vector3","x":1,"y":2,"z":3}` |
| Vector3i | `{"__gd":"Vector3i","x":1,"y":2,"z":3}` |
| Vector4 | `{"__gd":"Vector4","x":1,"y":2,"z":3,"w":4}` |
| Color | `{"__gd":"Color","r":1,"g":0.5,"b":0.5,"a":1}` |
| Rect2 | `{"__gd":"Rect2","position":V2,"size":V2}` |
| Rect2i | `{"__gd":"Rect2i","position":V2i,"size":V2i}` |
| Transform2D | `{"__gd":"Transform2D","x":V2,"y":V2,"origin":V2}` |
| Transform3D | `{"__gd":"Transform3D","basis":Basis,"origin":V3}` |
| Basis | `{"__gd":"Basis","x":[1,0,0],"y":[0,1,0],"z":[0,0,1]}` |
| Quaternion | `{"__gd":"Quaternion","x":0,"y":0,"z":0,"w":1}` |
| Plane | `{"__gd":"Plane","normal":V3,"d":0}` |
| AABB | `{"__gd":"AABB","position":V3,"size":V3}` |
| NodePath | `{"__gd":"NodePath","path":"Player/Sprite2D"}` |
| Object reference | `{"__gd":"Object","class":"Texture2D","path":"res://icon.svg"}` |
| RID | `{"__gd":"RID","id":"..."}` (read-only, decode unsupported) |

Decoding is permissive:

- `{"__gd":...}` exact form.
- `"Vector2(1, 2)"` / `"(1, 2)"` / `"1,2"` for vector-like properties.
- `[1,2]` / `[1,2,3]` arrays for vector-like properties.
- `"#ff0000"`, `"red"`, `[r,g,b(,a)]` for Color.
- Numbers for int/float (truncate/round as needed), `"true"`/`"false"` for bool.
- `null` resets an object property.

Encoding must be depth-limited (max 32) and must never recurse into `Node`/`Resource`
graphs: object references are encoded as `{"__gd":"Object","class":...,"path":...}` where
`path` is `resource_path` or the node path relative to the edited scene root.

## 5. Error codes

| Code | Meaning |
|---|---|
| -32700 | JSON parse error |
| -32600 | Invalid request |
| -32601 | Method not found |
| -32602 | Invalid params |
| -32603 | Internal error |
| 1001 | No active scene |
| 1002 | Node not found |
| 1003 | Property / signal not found |
| 1004 | Type or codec mismatch |
| 1005 | Path outside project / not res:// or user:// |
| 1006 | Resource not found |
| 1007 | Script compile error |
| 1008 | Runtime (game) not running |
| 1009 | Undo/redo failed |
| 1010 | Timeout |
| 1011 | Auth failed |
| 1012 | Permission denied / read-only |
| 1013 | File exists |
| 1014 | Unsupported in this Godot version |

## 6. Timeouts

| Operation | Timeout |
|---|---|
| connect | 3 s |
| default RPC | 10 s |
| scene save / script read | 20 s |
| execute / runtime.eval | 30 s |
| runtime.run | 60 s |
| screenshot | 15 s |
| runtime connect wait | 20 s |

## 7. Naming

- RPC methods: `group.action`, lower snake.
- GDScript files: snake_case; classes referenced via `preload`, not global `class_name`.
- MCP tools: `godot_<group>_<action>`; legacy 7 tools keep their v1 names.
- All JSON keys snake_case.
- Version: plugin `2.0.0`, protocol `2.0`.
