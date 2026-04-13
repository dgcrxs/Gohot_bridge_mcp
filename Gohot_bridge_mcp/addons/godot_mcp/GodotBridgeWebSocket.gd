@tool
extends EditorPlugin

const SETTINGS_PATH = "mcp_bridge/port"
const DEFAULT_PORT = 8080

var tcp_server: TCPServer
var peers: Dictionary = {}
var _port: int = DEFAULT_PORT

# UI 元素
var panel: Control
var status_label: Label
var port_input: LineEdit
var start_button: Button
var stop_button: Button
var save_button: Button
var settings_initialized: bool = false

func _enter_tree():
	_load_settings()
	_create_ui()
	_start_server()
	set_process(true)

func _exit_tree():
	set_process(false)
	_stop_server()
	_remove_ui()
	print("MCP Bridge: Plugin disabled")

func _load_settings():
	if not ProjectSettings.has_setting(SETTINGS_PATH):
		ProjectSettings.set_setting(SETTINGS_PATH, DEFAULT_PORT)
		var info = {
			"name": SETTINGS_PATH,
			"type": TYPE_INT,
			"hint": PROPERTY_HINT_RANGE,
			"hint_string": "1024,65535"
		}
		ProjectSettings.add_property_info(info)
		ProjectSettings.set_initial_value(SETTINGS_PATH, DEFAULT_PORT)
	_port = ProjectSettings.get_setting(SETTINGS_PATH)
	settings_initialized = true

func _create_ui():
	# 创建底部面板容器
	panel = VBoxContainer.new()
	panel.name = "MCP Bridge"
	
	# 标题
	var title = Label.new()
	title.text = "Godot MCP Bridge"
	title.add_theme_font_size_override("font_size", 16)
	panel.add_child(title)
	
	# 分隔线
	panel.add_child(HSeparator.new())
	
	# 状态行
	var status_row = HBoxContainer.new()
	status_label = Label.new()
	status_label.text = "状态: 未启动"
	status_row.add_child(status_label)
	panel.add_child(status_row)
	
	# 端口设置行
	var port_row = HBoxContainer.new()
	var port_label = Label.new()
	port_label.text = "端口: "
	port_row.add_child(port_label)
	
	port_input = LineEdit.new()
	port_input.text = str(_port)
	port_input.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	port_row.add_child(port_input)
	panel.add_child(port_row)
	
	# 按钮行
	var button_row = HBoxContainer.new()
	
	start_button = Button.new()
	start_button.text = "启动服务器"
	start_button.pressed.connect(_on_start_pressed)
	button_row.add_child(start_button)
	
	stop_button = Button.new()
	stop_button.text = "停止服务器"
	stop_button.disabled = true
	stop_button.pressed.connect(_on_stop_pressed)
	button_row.add_child(stop_button)
	
	save_button = Button.new()
	save_button.text = "保存端口并重启"
	save_button.pressed.connect(_on_save_pressed)
	button_row.add_child(save_button)
	
	panel.add_child(button_row)
	
	# 说明文本
	var hint = Label.new()
	hint.text = "修改端口后点击保存端口并重启生效。需确保 Godot 的 WebSocket 服务器正在运行，OpenCode 才能连接。"
	hint.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	panel.add_child(hint)
	
	# 将面板添加到编辑器底部
	add_control_to_bottom_panel(panel, "MCP Bridge")
	
	_update_ui_state()

func _remove_ui():
	if panel:
		remove_control_from_bottom_panel(panel)
		panel.queue_free()

func _update_ui_state():
	var running = tcp_server != null and tcp_server.is_listening()
	status_label.text = "状态: " + ("运行中 (端口 %d)" % _port if running else "已停止")
	start_button.disabled = running
	stop_button.disabled = not running
	port_input.editable = not running

func _start_server():
	if tcp_server:
		_stop_server()
	
	tcp_server = TCPServer.new()
	var err = tcp_server.listen(_port)
	if err != OK:
		push_error("MCP Bridge: 无法在端口 %d 启动服务器" % _port)
		status_label.text = "状态: 启动失败"
	else:
		print("MCP Bridge: WebSocket 服务器已在端口 %d 启动" % _port)
		_update_ui_state()

func _stop_server():
	# 关闭所有客户端连接
	for conn in peers.keys():
		var peer = peers[conn]
		if peer:
			peer.close()
	peers.clear()
	
	if tcp_server:
		tcp_server.stop()
		tcp_server = null
		print("MCP Bridge: WebSocket 服务器已停止")
	_update_ui_state()

func _on_start_pressed():
	_start_server()

func _on_stop_pressed():
	_stop_server()

func _on_save_pressed():
	var new_port = int(port_input.text)
	if new_port < 1024 or new_port > 65535:
		push_error("端口号必须在 1024-65535 之间")
		return
	
	_port = new_port
	ProjectSettings.set_setting(SETTINGS_PATH, _port)
	ProjectSettings.save()
	print("MCP Bridge: 端口配置已保存为 ", _port)
	
	# 重启服务器以应用新端口
	if tcp_server:
		_stop_server()
	_start_server()

# ---------- 以下为 WebSocket 通信处理，与原始代码相同 ----------

func _process(delta):
	if not tcp_server or not tcp_server.is_listening():
		return
	
	while tcp_server.is_connection_available():
		var connection = tcp_server.take_connection()
		var peer = WebSocketPeer.new()
		var accept_err = peer.accept_stream(connection)
		if accept_err != OK:
			print("MCP Bridge: accept_stream error " + str(accept_err))
			continue
		
		peers[connection] = peer
		print("MCP Bridge: 新连接已添加")
	
	var to_remove = []
	for conn in peers.keys():
		var peer = peers[conn]
		peer.poll()
		var state = peer.get_ready_state()
		
		if state == WebSocketPeer.STATE_OPEN:
			while peer.get_available_packet_count() > 0:
				var packet = peer.get_packet()
				if peer.was_string_packet():
					var msg = packet.get_string_from_utf8()
					print("MCP Bridge: RX ", msg)
					_handle_message(msg, peer)
		elif state == WebSocketPeer.STATE_CLOSED:
			var code = peer.get_close_code()
			var reason = peer.get_close_reason()
			print("MCP Bridge: 连接关闭 code=", code, " reason=", reason)
			to_remove.append(conn)
	
	for conn in to_remove:
		peers.erase(conn)

func _handle_message(data: String, peer: WebSocketPeer):
	var json = JSON.new()
	if json.parse(data) != OK:
		_respond(peer, 0, null, "Invalid JSON")
		return
	
	var payload = json.get_data()
	if typeof(payload) != TYPE_DICTIONARY:
		_respond(peer, 0, null, "Expected object")
		return
	
	if not payload.has("id") or not payload.has("method"):
		_respond(peer, 0, null, "Missing id or method")
		return
	
	var req_id = payload["id"]
	var method = payload["method"]
	var params = payload.get("params", {})
	
	var result = null
	var err_msg = ""
	
	match method:
		"get_scene_tree":
			result = _get_scene_tree()
		"add_node":
			result = _add_node(params)
		"get_node_properties":
			result = _get_node_properties(params)
		"set_node_property":
			result = _set_node_property(params)
		"execute_script":
			result = _execute_script(params)
		"get_selected_nodes":
			result = _get_selected_nodes()
		"get_editor_info":
			result = _get_editor_info()
		"list_node_types":
			result = _list_node_types()
		_:
			err_msg = "Unknown method: " + str(method)
	
	_respond(peer, req_id, result, err_msg)

func _respond(peer: WebSocketPeer, req_id, result, err_msg: String):
	var response = {"jsonrpc": "2.0", "id": req_id}
	if err_msg != "":
		response["error"] = {"code": -32600, "message": err_msg}
	else:
		response["result"] = result if result != null else {}
	
	var json_str = JSON.stringify(response)
	peer.send_text(json_str)
	print("MCP Bridge: Sent ", json_str)

# ---------- 业务逻辑函数，与原代码保持一致 ----------

func _get_root():
	return get_editor_interface().get_edited_scene_root()

func _build_tree(node):
	var root = _get_root()
	var path_str = ""
	if root:
		path_str = root.get_path_to(node)
	
	var data = {
		"name": node.name,
		"type": node.get_class(),
		"path": path_str,
		"children": []
	}
	
	for child in node.get_children():
		data["children"].append(_build_tree(child))
	
	return data

func _get_scene_tree():
	var root = _get_root()
	if not root:
		return {"error": "No active scene"}
	return {"scene_tree": _build_tree(root)}

func _add_node(params):
	var node_type = params.get("type", "")
	if node_type == "":
		return {"error": "Missing type"}
	
	var node_name = params.get("name", node_type)
	var parent_path = params.get("parent_path", "")
	
	var root = _get_root()
	if not root:
		return {"error": "No active scene"}
	
	var parent_node = null
	var sel = get_editor_interface().get_selection()
	var selected = sel.get_selected_nodes()
	
	if parent_path != "":
		parent_node = root.get_node(parent_path)
	elif selected.size() > 0:
		parent_node = selected[0]
	else:
		return {"error": "No parent specified"}
	
	if not parent_node:
		return {"error": "Parent not found"}
	
	var new_node = ClassDB.instantiate(node_type)
	if not new_node:
		return {"error": "Cannot create " + node_type}
	
	new_node.name = node_name
	parent_node.add_child(new_node)
	new_node.owner = root
	
	get_editor_interface().mark_scene_as_unsaved()
	
	return {
		"success": true,
		"node": {
			"name": new_node.name,
			"type": new_node.get_class(),
			"path": root.get_path_to(new_node)
		}
	}

func _get_node_properties(params):
	var node_path = params.get("path", "")
	if node_path == "":
		return {"error": "Missing path"}
	
	var root = _get_root()
	if not root:
		return {"error": "No active scene"}
	
	var target = root.get_node(node_path)
	if not target:
		return {"error": "Node not found"}
	
	var props = {}
	for p in target.get_property_list():
		var pname = p["name"]
		if not pname.begins_with("_"):
			if p["usage"] & PROPERTY_USAGE_STORAGE:
				props[pname] = target.get(pname)
	
	return {"node_path": node_path, "properties": props}

func _set_node_property(params):
	var node_path = params.get("path", "")
	var prop_name = params.get("property", "")
	var value = params.get("value")
	
	if node_path == "" or prop_name == "":
		return {"error": "Missing path or property"}
	
	var root = _get_root()
	if not root:
		return {"error": "No active scene"}
	
	var target = root.get_node(node_path)
	if not target:
		return {"error": "Node not found"}
	
	target.set(prop_name, value)
	get_editor_interface().mark_scene_as_unsaved()
	
	return {"success": true, "node_path": node_path, "property": prop_name}

func _execute_script(params):
	var code = params.get("code", "")
	if code == "":
		return {"error": "Missing code"}
	
	var root = _get_root()
	if not root:
		return {"error": "No active scene"}
	
	var script = GDScript.new()
	script.source_code = "extends Node\nfunc _run():\n\t" + code
	script.reload()
	
	var temp = Node.new()
	temp.set_script(script)
	root.add_child(temp)
	
	var result = null
	if temp.has_method("_run"):
		result = temp.call("_run")
	
	root.remove_child(temp)
	temp.queue_free()
	
	return {"success": true, "result": result if result != null else "done"}

func _get_selected_nodes():
	var sel = get_editor_interface().get_selection()
	var nodes = sel.get_selected_nodes()
	var root = _get_root()
	var result = []
	
	for n in nodes:
		var path_str = ""
		if root:
			path_str = root.get_path_to(n)
		result.append({
			"name": n.name,
			"type": n.get_class(),
			"path": path_str
		})
	
	return {"selected_nodes": result}

func _get_editor_info():
	var root = _get_root()
	var info = Engine.get_version_info()
	return {
		"version": info.get("string", "unknown"),
		"system": OS.get_name(),
		"has_scene": root != null,
		"scene_path": root.scene_file_path if root else ""
	}

func _list_node_types():
	var classes = []
	for cname in ClassDB.get_class_list():
		if cname in ["Object", "Node", "Resource", "RefCounted", "Script", "GDScript", "VisualScript"]:
			continue
		if ClassDB.can_instantiate(cname):
			classes.append(cname)
	classes.sort()
	return {"node_types": classes}