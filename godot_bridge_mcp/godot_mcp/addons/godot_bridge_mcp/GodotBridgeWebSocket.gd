@tool
extends EditorPlugin

var tcp_server: TCPServer
var peers: Dictionary = {}
var _port = 8080

func _enter_tree():
	tcp_server = TCPServer.new()
	var err = tcp_server.listen(_port)
	if err != OK:
		push_error("MCP Bridge: Failed to start on port " + str(_port))
	else:
		print("MCP Bridge: WebSocket server started on port " + str(_port))
	set_process(true)

func _exit_tree():
	set_process(false)
	var conn_keys = peers.keys()
	for conn in conn_keys:
		var peer = peers[conn]
		if peer:
			peer.close()
	peers.clear()
	if tcp_server:
		tcp_server.stop()
	print("MCP Bridge: WebSocket server stopped")

func _process(delta):
	if not tcp_server:
		return
	
	if tcp_server.is_connection_available():
		var connection = tcp_server.take_connection()
		var peer = WebSocketPeer.new()
		var accept_err = peer.accept_stream(connection)
		if accept_err != OK:
			print("MCP Bridge: accept_stream error " + str(accept_err))
			return
		
		peers[connection] = peer
		print("MCP Bridge: New connection added, initial state=" + str(peer.get_ready_state()))
	
	var to_remove = []
	var conn_keys = peers.keys()
	for conn in conn_keys:
		var peer = peers[conn]
		peer.poll()
		var state = peer.get_ready_state()
		
		if state != peer.get_meta("last_state", -1):
			print("MCP Bridge: Peer state changed to " + str(state))
			peer.set_meta("last_state", state)
		
		if state == WebSocketPeer.STATE_OPEN:
			while peer.get_available_packet_count() > 0:
				var packet = peer.get_packet()
				if peer.was_string_packet():
					var msg = packet.get_string_from_utf8()
					print("MCP Bridge: RX " + msg)
					_handle_message(msg, peer)
		elif state == WebSocketPeer.STATE_CLOSED:
			var code = peer.get_close_code()
			var reason = peer.get_close_reason()
			print("MCP Bridge: Closed code=" + str(code) + " reason=" + reason)
			to_remove.append(conn)
	
	for conn in to_remove:
		peers.erase(conn)

func _handle_message(data: String, peer: WebSocketPeer):
	var parser = JSON.new()
	if parser.parse(data) != OK:
		_respond(peer, 0, null, "Invalid JSON")
		return
	
	var payload = parser.get_data()
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
	
	if method == "get_scene_tree":
		result = _get_scene_tree()
	elif method == "add_node":
		result = _add_node(params)
	elif method == "get_node_properties":
		result = _get_node_properties(params)
	elif method == "set_node_property":
		result = _set_node_property(params)
	elif method == "execute_script":
		result = _execute_script(params)
	elif method == "get_selected_nodes":
		result = _get_selected_nodes()
	elif method == "get_editor_info":
		result = _get_editor_info()
	elif method == "list_node_types":
		result = _list_node_types()
	else:
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
	print("MCP Bridge: Sent " + json_str)

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
	
	var count = node.get_child_count()
	var i = 0
	while i < count:
		var child = node.get_child(i)
		data["children"].append(_build_tree(child))
		i += 1
	
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
	var prop_list = target.get_property_list()
	var i = 0
	while i < prop_list.size():
		var p = prop_list[i]
		var pname = p["name"]
		if not pname.begins_with("_"):
			if p["usage"] & PROPERTY_USAGE_STORAGE:
				props[pname] = target.get(pname)
		i += 1
	
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
	script.set_source_code("extends Node\nfunc _run():\n\t" + code)
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
	
	var i = 0
	while i < nodes.size():
		var n = nodes[i]
		var path_str = ""
		if root:
			path_str = root.get_path_to(n)
		result.append({
			"name": n.name,
			"type": n.get_class(),
			"path": path_str
		})
		i += 1
	
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
	var all_classes = ClassDB.get_class_list()
	
	var skip = ["Object", "Node", "Resource", "RefCounted", "Script", "GDScript", "VisualScript"]
	
	var i = 0
	while i < all_classes.size():
		var cname = all_classes[i]
		var should_skip = false
		
		var j = 0
		while j < skip.size():
			if cname == skip[j]:
				should_skip = true
				break
			j += 1
		
		if not should_skip:
			if ClassDB.can_instantiate(cname):
				classes.append(cname)
		i += 1
	
	classes.sort()
	return {"node_types": classes}
