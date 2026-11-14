"""Integration tests against a real headless Godot editor (PROTOCOL.md end to end).

These tests talk to the editor through the real WebSocket bridge, using the
handshake file of ``GODOT_MCP_PROJECT`` (default ``/tmp/gdproj``). They are
skipped when no editor answers.

Rules followed by every test here:

* a unique node/script/file name per test, registered on the ``editor`` handle
  and deleted in teardown;
* shared nodes (``Player``, ``Collision``) are never mutated without restoring;
* ``main.tscn`` is never saved, so the project on disk stays untouched.
"""

import asyncio
import json
import socket
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

import pytest
import websockets

from tests.conftest import FIXTURE_PROJECT, GODOT_BIN, PROJECT_DIR, read_handshake, tcp_reachable

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def unique(prefix: str = "QAT") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


async def position_of(handle: Any, path: str, prop: str = "position") -> Any:
    payload = await handle.ok("godot_get_property", {"path": path, "property": prop})
    return payload["value"]


async def root_children(handle: Any) -> List[str]:
    payload = await handle.ok("godot_get_scene_tree", {"depth": 1})
    return [child["name"] for child in payload["root"]["children"]]


async def raw_ws(port: int, timeout: float = 5.0) -> Any:
    return await websockets.connect(f"ws://127.0.0.1:{port}", open_timeout=timeout)


# --------------------------------------------------------------------------- #
# system group
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ping_reports_protocol_2_and_auth_requirement(editor: Any) -> None:
    """``godot_ping`` identifies the bridge without authenticating twice."""
    payload = await editor.ok("godot_ping")

    assert payload["pong"] is True
    assert payload["protocol"] == "2.0"
    assert payload["project"]
    assert isinstance(payload["pid"], int)
    assert payload["requires_auth"] is True


@pytest.mark.asyncio
async def test_editor_info_describes_the_open_scene(editor: Any) -> None:
    """PROTOCOL.md §3.1 ``get_editor_info`` fields are all present."""
    payload = await editor.ok("get_editor_info")

    assert payload["godot_version"].startswith("4.")
    assert payload["system"]
    assert payload["project_name"]
    assert payload["project_path"]
    assert payload["scene_path"].endswith(".tscn")
    assert isinstance(payload["scene_modified"], bool)
    assert isinstance(payload["open_scenes"], list) and payload["open_scenes"]
    assert payload["open_scenes"][0]["current"] is True
    assert isinstance(payload["is_playing"], bool)


@pytest.mark.asyncio
async def test_capabilities_cover_the_documented_groups(editor: Any) -> None:
    """``get_capabilities`` advertises the v2 method surface."""
    payload = await editor.ok("godot_get_capabilities")

    assert payload["protocol"] == "2.0"
    assert payload["plugin_version"] == "2.0.0"
    methods = set(payload["methods"])
    for expected in ("ping", "auth", "scene.get_tree", "node.add", "prop.set", "editor.undo", "execute.gdscript"):
        assert expected in methods, f"{expected} missing from the capability list"
    groups = set(payload["groups"])
    assert {"system", "scene", "node", "prop", "script", "res", "editor"}.issubset(groups)


@pytest.mark.asyncio
async def test_current_scene_and_open_scenes(editor: Any) -> None:
    """The edited scene is ``main.tscn`` with a ``Main`` root."""
    current = await editor.ok("godot_get_current_scene")
    assert current["path"] == "res://main.tscn"
    assert current["root_name"] == "Main"
    assert current["root_type"] == "Node2D"

    opened = await editor.ok("godot_list_open_scenes")
    assert any(scene["path"] == "res://main.tscn" and scene["current"] for scene in opened["scenes"])


# --------------------------------------------------------------------------- #
# scene / node
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scene_tree_matches_the_project_hierarchy(editor: Any) -> None:
    """The tree exposes Main > Player > Collision and no internal nodes."""
    payload = await editor.ok("godot_get_scene_tree", {"depth": -1})

    root = payload["root"]
    assert root["name"] == "Main"
    assert root["type"] == "Node2D"
    assert root["path"] in {".", ""}
    children = {child["name"]: child for child in root["children"]}
    assert "Player" in children, f"unexpected root children: {sorted(children)}"
    player = children["Player"]
    assert player["type"] == "CharacterBody2D"
    assert player["owner"] == "."
    assert player["path"] == "Player"
    assert [child["name"] for child in player["children"]] == ["Collision"]
    assert payload["count"] >= 3
    assert "@" not in json.dumps(payload["root"]), "internal nodes leaked into the tree"


@pytest.mark.asyncio
async def test_scene_modified_flag_tracks_unsaved_edits(editor: Any) -> None:
    """``scene.current.modified`` must flip once the scene differs from disk."""
    before = await editor.ok("godot_get_current_scene")
    assert before["modified"] is False, "guard: the scene starts clean"

    node = await editor.new_node("Node2D")
    during = await editor.ok("godot_get_current_scene")
    info = await editor.ok("get_editor_info")
    assert during["modified"] is True, "adding a node did not mark the scene as modified"
    assert info["scene_modified"] is True

    await editor.ok("godot_delete_node", {"path": node})
    editor.temp_nodes.remove(node)


@pytest.mark.asyncio
async def test_scene_tree_depth_limits_recursion(editor: Any) -> None:
    """``depth`` truncates the response."""
    shallow = await editor.ok("godot_get_scene_tree", {"depth": 0})
    assert shallow["root"]["children"] == []
    assert all("children" not in child or child.get("children") == [] for child in shallow["root"]["children"])


@pytest.mark.asyncio
async def test_add_set_undo_redo_round_trip(editor: Any) -> None:
    """The core promise: one tool call = one undo step, redo restores it."""
    node = await editor.new_node("Node2D")
    new_position = {"__gd": "Vector2", "x": 12.0, "y": 34.0}

    set_payload = await editor.ok(
        "godot_set_property", {"path": node, "property": "position", "value": new_position}
    )
    assert set_payload["old"] == {"__gd": "Vector2", "x": 0.0, "y": 0.0}
    assert set_payload["new"] == new_position
    assert await position_of(editor, node) == new_position

    undo = await editor.ok("godot_undo", {"steps": 1})
    assert undo["done"] == 1
    assert await position_of(editor, node) == {"__gd": "Vector2", "x": 0.0, "y": 0.0}, "undo did not revert the write"
    # One undo must only revert the property write, not the node creation.
    tree = await editor.ok("godot_get_scene_tree", {"depth": 1})
    assert node in [child["name"] for child in tree["root"]["children"]]

    redo = await editor.ok("godot_redo", {"steps": 1})
    assert redo["done"] == 1
    assert await position_of(editor, node) == new_position, "redo did not restore the new value"


@pytest.mark.asyncio
async def test_multi_step_undo(editor: Any) -> None:
    """``steps`` reverts several tool calls, newest first."""
    node = await editor.new_node("Node2D")
    first = {"__gd": "Vector2", "x": 1.0, "y": 1.0}
    second = {"__gd": "Vector2", "x": 2.0, "y": 2.0}
    await editor.ok("godot_set_property", {"path": node, "property": "position", "value": first})
    await editor.ok("godot_set_property", {"path": node, "property": "position", "value": second})
    assert await position_of(editor, node) == second

    payload = await editor.ok("godot_undo", {"steps": 2})
    assert payload["done"] == 2
    assert await position_of(editor, node) == {"__gd": "Vector2", "x": 0.0, "y": 0.0}


@pytest.mark.asyncio
async def test_node_remove_and_undo_restores_owner_and_index(editor: Any) -> None:
    """Removing a node is undoable with its sibling index and owner intact."""
    first = await editor.new_node("Node2D")
    second = await editor.new_node("Node2D")
    before = await root_children(editor)
    assert before.index(first) < before.index(second)

    removed = await editor.ok("godot_delete_node", {"path": first})
    assert removed["removed"] is True
    after_remove = await root_children(editor)
    assert first not in after_remove

    undo = await editor.ok("godot_undo", {"steps": 1})
    assert undo["done"] == 1
    assert await root_children(editor) == before, "undo did not restore the node at its original index"

    restored = await editor.ok("godot_get_node", {"path": first})
    assert restored["owner"] == ".", "restored node lost its scene owner (would not be saved)"
    assert restored["type"] == "Node2D"


@pytest.mark.asyncio
async def test_rename_move_duplicate_and_undo(editor: Any) -> None:
    """Node tree edits are single undo steps each."""
    parent = await editor.new_node("Node2D")
    child = await editor.new_node("Node2D", parent=parent)

    old_name = child.rsplit("/", 1)[-1]
    new_name = f"{old_name}_r"
    renamed = await editor.ok("godot_rename_node", {"path": child, "new_name": new_name})
    assert renamed["old_name"] == old_name, "old_name must be the previous node name"
    assert renamed["path"].endswith(new_name)
    new_path = renamed["path"]

    moved = await editor.ok("godot_move_node", {"path": new_path, "new_parent": ""})
    assert moved["parent"] in {"", "."}, f"scene root must be reported as '' or '.', got {moved['parent']!r}"
    moved_path = moved["path"]
    assert moved_path == new_name, f"node.move must report the new path, got {moved_path!r}"
    editor.temp_nodes.append(moved_path)

    duplicated = await editor.ok("godot_duplicate_node", {"path": moved_path})
    editor.temp_nodes.append(duplicated["path"])
    assert duplicated["path"] != moved_path

    payload = await editor.ok("godot_undo", {"steps": 3})
    assert payload["done"] == 3
    tree = await editor.ok("godot_get_scene_tree", {"depth": -1})
    assert duplicated["path"] not in json.dumps(tree), "undo did not remove the duplicate"


@pytest.mark.asyncio
async def test_node_find_by_type_and_pattern(editor: Any) -> None:
    """``godot_find_nodes`` filters by type and glob pattern."""
    by_type = await editor.ok("godot_find_nodes", {"type": "CharacterBody2D"})
    assert "Player" in [node["name"] for node in by_type["nodes"]]

    by_pattern = await editor.ok("godot_find_nodes", {"pattern": "Pla*"})
    assert "Player" in [node["name"] for node in by_pattern["nodes"]]

    limited = await editor.ok("godot_find_nodes", {"limit": 1})
    assert len(limited["nodes"]) <= 1
    assert limited["count"] >= 1


@pytest.mark.asyncio
async def test_node_get_reports_script_and_children(editor: Any) -> None:
    """``godot_get_node`` exposes type, owner, groups and child count."""
    payload = await editor.ok("godot_get_node", {"path": "Player"})

    assert payload["name"] == "Player"
    assert payload["type"] == "CharacterBody2D"
    assert payload["path"] == "Player"
    assert payload["owner"] == "."
    assert payload["children_count"] == 1
    assert isinstance(payload["groups"], list)
    assert payload["script"] == ""


@pytest.mark.asyncio
async def test_absolute_and_relative_paths_are_equivalent(editor: Any) -> None:
    """PROTOCOL.md §3.3: ``Player``, ``/root/Player`` and ``.`` all resolve."""
    relative = await editor.ok("godot_get_node", {"path": "Player"})
    absolute = await editor.ok("godot_get_node", {"path": "/root/Player"})
    root = await editor.ok("godot_get_node", {"path": "."})

    assert relative["type"] == absolute["type"] == "CharacterBody2D"
    assert root["name"] == "Main"


# --------------------------------------------------------------------------- #
# property group
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_property_list_and_get_all(editor: Any) -> None:
    """``prop.list`` describes properties, ``prop.get_all`` returns values."""
    listed = await editor.ok("godot_list_properties", {"path": "Player"})
    names = [prop["name"] for prop in listed["properties"]]
    assert "position" in names and "modulate" in names
    entry = next(prop for prop in listed["properties"] if prop["name"] == "position")
    assert entry["type"] in {"Vector2", "TYPE_VECTOR2", 5} or str(entry["type"])

    values = await editor.ok("godot_get_properties", {"path": "Player"})
    assert "position" in values["properties"]


@pytest.mark.asyncio
async def test_set_many_applies_all_values_in_one_step(editor: Any) -> None:
    """``prop.set_many`` is one undo action for all properties."""
    node = await editor.new_node("Node2D")
    await editor.ok(
        "godot_set_properties",
        {
            "path": node,
            "values": {
                "position": {"__gd": "Vector2", "x": 3.0, "y": 4.0},
                "rotation": 0.5,
                "visible": False,
            },
        },
    )
    assert await position_of(editor, node) == {"__gd": "Vector2", "x": 3.0, "y": 4.0}
    assert (await editor.ok("godot_get_property", {"path": node, "property": "visible"}))["value"] is False

    await editor.ok("godot_undo", {"steps": 1})
    assert await position_of(editor, node) == {"__gd": "Vector2", "x": 0.0, "y": 0.0}
    assert (await editor.ok("godot_get_property", {"path": node, "property": "visible"}))["value"] is True


@pytest.mark.asyncio
async def test_vector2_vector3_transform2d_nodepath_round_trip(editor: Any) -> None:
    """The value codec survives an encode/decode round trip for 4 types."""
    node2d = await editor.new_node("Node2D")
    node3d = await editor.new_node("Node3D")
    animation = await editor.new_node("AnimationPlayer")

    cases = [
        (node2d, "position", {"__gd": "Vector2", "x": 1.5, "y": 2.5}),
        (node3d, "position", {"__gd": "Vector3", "x": 1.5, "y": 2.5, "z": 3.5}),
        (
            node2d,
            "transform",
            {
                "__gd": "Transform2D",
                "x": {"__gd": "Vector2", "x": 1.0, "y": 0.0},
                "y": {"__gd": "Vector2", "x": 0.0, "y": 1.0},
                "origin": {"__gd": "Vector2", "x": 7.0, "y": 8.0},
            },
        ),
        (animation, "root_node", {"__gd": "NodePath", "path": "../Player"}),
    ]
    for node, prop, value in cases:
        await editor.ok("godot_set_property", {"path": node, "property": prop, "value": value})
        stored = await position_of(editor, node, prop)
        assert stored == value, f"{prop} round trip changed the value: {stored} != {value}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value, expected",
    [
        ("Vector2(1, 2)", {"__gd": "Vector2", "x": 1.0, "y": 2.0}),
        ("(3, 4)", {"__gd": "Vector2", "x": 3.0, "y": 4.0}),
        ("5,6", {"__gd": "Vector2", "x": 5.0, "y": 6.0}),
        ([7, 8], {"__gd": "Vector2", "x": 7.0, "y": 8.0}),
    ],
)
async def test_permissive_vector_forms_are_accepted(editor: Any, value: Any, expected: Dict[str, Any]) -> None:
    """PROTOCOL.md §4: string and array shorthand is accepted for vectors."""
    node = await editor.new_node("Node2D")

    payload = await editor.ok("godot_set_property", {"path": node, "property": "position", "value": value})
    assert payload["new"] == expected
    assert await position_of(editor, node) == expected


@pytest.mark.asyncio
async def test_color_round_trip(editor: Any) -> None:
    """PROTOCOL.md §4: Color is encoded as ``{r,g,b,a}`` and must decode back."""
    node = await editor.new_node("Node2D")
    value = {"__gd": "Color", "r": 0.25, "g": 0.5, "b": 0.75, "a": 1.0}

    payload = await editor.ok("godot_set_property", {"path": node, "property": "modulate", "value": value})
    assert payload["new"] == value, f"prop.set echoed a corrupted value: {payload['new']}"

    stored = await position_of(editor, node, "modulate")
    assert stored == value, f"Color round trip corrupted the value: {stored} != {value}"


@pytest.mark.asyncio
async def test_undecodable_value_reports_1004(editor: Any) -> None:
    """A value that cannot be decoded must fail with 1004 and change nothing."""
    node = await editor.new_node("Node2D")
    original = {"__gd": "Vector2", "x": 7.0, "y": 9.0}
    await editor.ok("godot_set_property", {"path": node, "property": "position", "value": original})

    error = await editor.failure(
        "godot_set_property", {"path": node, "property": "position", "value": "definitely not a vector"}
    )
    assert error["code"] == 1004, f"expected 1004 (codec mismatch), got {error}"
    assert error["hint"]
    assert await position_of(editor, node) == original, "a rejected write still mutated the property"


# --------------------------------------------------------------------------- #
# error diagnostics
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_missing_node_is_1002_with_a_hint(editor: Any) -> None:
    """A wrong node path must be diagnosable, not just "error"."""
    missing = unique("Ghost")

    error = await editor.failure("godot_get_node", {"path": missing})
    assert error["code"] == 1002
    assert missing in error["message"]
    assert error["hint"], "1002 without a hint is not actionable"
    assert error["context"].get("path") == missing


@pytest.mark.asyncio
async def test_missing_property_is_1003_with_a_hint(editor: Any) -> None:
    """A wrong property name reports 1003 and where to look."""
    error = await editor.failure("godot_get_property", {"path": "Player", "property": "no_such_property"})

    assert error["code"] == 1003
    assert "no_such_property" in error["message"]
    assert error["hint"]
    assert error["context"].get("property") == "no_such_property"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "res://../outside.txt", "/root/.ssh/id_rsa"],
)
async def test_reading_outside_the_project_is_1005(editor: Any, path: str) -> None:
    """PROTOCOL.md §3.7: anything outside res://user:// is refused with 1005."""
    error = await editor.failure("godot_read_file", {"path": path})
    assert error["code"] == 1005, f"{path} was not refused: {error}"
    assert error["hint"]


@pytest.mark.asyncio
async def test_writing_outside_the_project_is_1005(editor: Any) -> None:
    """Writes outside the project are refused too."""
    error = await editor.failure("godot_write_file", {"path": "/tmp/qa_escape.txt", "content": "nope"})
    assert error["code"] == 1005
    assert not Path("/tmp/qa_escape.txt").exists(), "the editor wrote outside the project"


@pytest.mark.asyncio
async def test_directory_listing_outside_the_project_is_1005(editor: Any) -> None:
    """``res.list`` / ``fs.list_dir`` are path-guarded as well."""
    for tool, arguments in (
        ("godot_list_directory", {"path": "/etc"}),
        ("godot_list_resources", {"dir": "/etc"}),
    ):
        error = await editor.failure(tool, arguments)
        assert error["code"] == 1005, f"{tool} allowed {arguments}"


# --------------------------------------------------------------------------- #
# script group
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_script_create_read_write_check(editor: Any) -> None:
    """The full script file lifecycle keeps the content byte-identical."""
    path = await editor.new_script("extends Node2D\n\nvar hp := 10\n")

    read = await editor.ok("godot_read_script", {"path": path})
    assert read["source"] == "extends Node2D\n\nvar hp := 10\n"
    assert read["lines"] >= 3

    updated = "extends Node2D\n\nvar hp := 20\n"
    written = await editor.ok("godot_write_script", {"path": path, "source": updated})
    assert written["bytes"] == len(updated.encode("utf-8"))
    assert (await editor.ok("godot_read_script", {"path": path}))["source"] == updated

    assert (await editor.ok("godot_check_script", {"path": path}))["ok"] is True
    broken = await editor.call("godot_check_script", {"source": "extends Node2D\nfunc _ready(:\n\tpass"})
    assert broken["ok"] is False, "a syntax error must be reported by the checker"
    assert broken["errors"] and broken["errors"][0]["message"]


@pytest.mark.asyncio
async def test_script_attach_and_detach(editor: Any) -> None:
    """Attaching a script is visible on the node and reversible."""
    node = await editor.new_node("Node2D")
    path = await editor.new_script("extends Node2D\n\nfunc ping() -> int:\n\treturn 1\n")

    attached = await editor.ok("godot_attach_script", {"node_path": node, "script_path": path})
    assert attached["attached"] is True
    node_info = await editor.ok("godot_get_node", {"path": node})
    assert node_info["script"] == path

    detached = await editor.ok("godot_detach_script", {"node_path": node})
    assert detached["detached"] is True
    assert (await editor.ok("godot_get_node", {"path": node}))["script"] == ""


@pytest.mark.asyncio
async def test_script_list_and_delete(editor: Any) -> None:
    """``script.list`` sees new files and ``script.delete`` removes them."""
    path = await editor.new_script("extends Node\n")
    listed = await editor.ok("godot_list_scripts", {"dir": "res://", "recursive": True})
    paths = [script["path"] for script in listed["scripts"]]
    assert path in paths

    deleted = await editor.ok("godot_delete_script", {"path": path})
    assert deleted["deleted"] is True
    editor.temp_scripts.remove(path)
    error = await editor.failure("godot_read_script", {"path": path})
    assert error["code"] == 1006


# --------------------------------------------------------------------------- #
# signal group (RPC only: no MCP tool exposes signals yet)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_signal_connect_list_disconnect(editor: Any) -> None:
    """PROTOCOL.md §3.5 connect/list/disconnect round trip."""
    node = await editor.new_node("Node2D")
    script = await editor.new_script(
        "extends Node2D\n\n\nfunc _on_qa_ready() -> void:\n\tpass\n"
    )
    await editor.ok("godot_attach_script", {"node_path": node, "script_path": script})

    connected = await editor.rpc(
        "signal.connect", {"path": node, "signal": "ready", "target_path": node, "method": "_on_qa_ready"}
    )
    assert connected["connected"] is True

    listed = await editor.rpc("signal.list", {"path": node})
    ready = next(entry for entry in listed["signals"] if entry["name"] == "ready")
    assert {"target": node, "method": "_on_qa_ready", "flags": 0} in ready["connections"]

    disconnected = await editor.rpc(
        "signal.disconnect", {"path": node, "signal": "ready", "target_path": node, "method": "_on_qa_ready"}
    )
    assert disconnected["disconnected"] is True
    listed_again = await editor.rpc("signal.list", {"path": node})
    ready_again = next(entry for entry in listed_again["signals"] if entry["name"] == "ready")
    assert all(item["method"] != "_on_qa_ready" for item in ready_again["connections"])


@pytest.mark.asyncio
async def test_signal_connect_to_a_missing_method_is_rejected(editor: Any) -> None:
    """Connecting to a non-existent handler must fail with a hint."""
    node = await editor.new_node("Node2D")
    from server.errors import GodotError

    with pytest.raises(GodotError) as excinfo:
        await editor.rpc(
            "signal.connect",
            {"path": node, "signal": "ready", "target_path": node, "method": "_no_such_handler"},
        )
    assert excinfo.value.code in {-32602, 1003}
    assert excinfo.value.hint


# --------------------------------------------------------------------------- #
# resource / filesystem group
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_file_write_read_round_trip(editor: Any) -> None:
    """``file.write`` + ``file.read`` keep UTF-8 content intact."""
    content = f"qa marker {uuid.uuid4().hex}\nsecond line\n"
    path = await editor.new_file(content)

    payload = await editor.ok("godot_read_file", {"path": path})
    assert payload["content"] == content
    assert payload["encoding"] == "utf-8"
    assert payload["size"] == len(content.encode("utf-8"))


@pytest.mark.asyncio
async def test_search_files_finds_written_content(editor: Any) -> None:
    """``fs.search`` greps the project and reports path and line."""
    marker = f"qamarker{uuid.uuid4().hex}"
    path = await editor.new_file(f"noise\n{marker}\n")

    payload = await editor.ok("godot_search_files", {"query": marker, "glob": "*.txt;*.gd"})
    assert payload["count"] >= 1
    assert any(match["path"] == path for match in payload["matches"])


@pytest.mark.asyncio
async def test_resource_list_info_and_dependencies(editor: Any) -> None:
    """``res.list``/``res.info``/``res.dependencies`` describe real resources."""
    listed = await editor.ok("godot_list_resources", {"dir": "res://", "recursive": False})
    paths = [entry["path"] for entry in listed["entries"]]
    assert "res://main.tscn" in paths
    main_entry = next(entry for entry in listed["entries"] if entry["path"] == "res://main.tscn")
    assert main_entry["type"] == "PackedScene"

    info = await editor.ok("godot_get_resource_info", {"path": "res://main.tscn"})
    assert info["type"] == "PackedScene"
    assert info["resource_path"] == "res://main.tscn"

    # A scene that references an external script must report it as a dependency.
    dependency = f"res://{unique('qa_dep')}.gd"
    await editor.ok("godot_create_script", {"path": dependency, "source": "extends Node2D\n"})
    editor.temp_scripts.append(dependency)
    scene_path = f"res://{unique('qa_dep_scene')}.tscn"
    scene_source = (
        "[gd_scene load_steps=2 format=3]\n\n"
        f'[ext_resource type="Script" path="{dependency}" id="1_qa"]\n\n'
        '[node name="QaDep" type="Node2D"]\nscript = ExtResource("1_qa")\n'
    )
    await editor.ok("godot_write_file", {"path": scene_path, "content": scene_source})
    editor.temp_files.append(scene_path)

    deps = await editor.ok("godot_get_resource_dependencies", {"path": scene_path})
    assert any(item["path"] == dependency for item in deps["dependencies"]), deps


@pytest.mark.asyncio
async def test_resource_create_and_delete(editor: Any) -> None:
    """``res.create``/``res.load``/``res.delete`` work on a throw-away file."""
    path = f"res://{unique('qa_res')}.tres"
    created = await editor.ok("godot_create_resource", {"path": path, "type": "Resource"})
    editor.temp_files.append(path)
    assert created["type"] == "Resource"

    loaded = await editor.ok("godot_load_resource", {"path": path})
    assert loaded["type"] == "Resource"
    assert isinstance(loaded["properties"], dict)

    deleted = await editor.ok("godot_delete_resource", {"path": path})
    assert deleted["deleted"] is True
    editor.temp_files.remove(path)


@pytest.mark.asyncio
async def test_list_directory_reports_entries(editor: Any) -> None:
    """``fs.list_dir`` lists the project root."""
    payload = await editor.ok("godot_list_directory", {"path": "res://"})

    names = [entry["name"] for entry in payload["entries"]]
    assert "project.godot" in names
    assert any(entry["dir"] for entry in payload["entries"])


# --------------------------------------------------------------------------- #
# docs / introspection
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_docs_class_describes_a_godot_type(editor: Any) -> None:
    """``docs.class`` returns inheritance, methods, properties and signals."""
    payload = await editor.ok("godot_get_class_info", {"class_name": "CharacterBody2D"})

    assert payload["name"] == "CharacterBody2D"
    assert payload["inherits"] == "PhysicsBody2D"
    assert payload["instantiable"] is True
    method_names = [method["name"] for method in payload["methods"]]
    assert "move_and_slide" in method_names
    assert payload["properties"], "documented properties must not be empty"

    timer = await editor.ok("godot_get_class_info", {"class_name": "Timer"})
    assert "timeout" in [signal["name"] for signal in timer["signals"]]


@pytest.mark.asyncio
async def test_docs_search_finds_node_types(editor: Any) -> None:
    """``docs.search`` powers node type selection."""
    payload = await editor.ok("godot_search_classes", {"query": "CollisionShape"})
    names = [entry["name"] for entry in payload["classes"]]

    assert "CollisionShape2D" in names
    instantiable_only = await editor.ok(
        "godot_search_classes", {"query": "Collision", "instantiable_only": True}
    )
    assert all(entry["instantiable"] for entry in instantiable_only["classes"])


# --------------------------------------------------------------------------- #
# execute / editor group
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_execute_script_returns_a_value(editor: Any) -> None:
    """``execute.gdscript`` runs multi-line code and encodes the result."""
    payload = await editor.ok(
        "godot_execute_script", {"code": "var total = 0\nfor i in range(5):\n\ttotal += i\nreturn total"}
    )
    assert payload["result"] == 10
    assert "elapsed_ms" in payload


@pytest.mark.asyncio
async def test_execute_script_reports_compile_errors(editor: Any) -> None:
    """A broken snippet returns 1007 with the compiler message."""
    error = await editor.failure("godot_execute_script", {"code": "this is not gdscript"})
    assert error["code"] == 1007
    assert error["hint"]


@pytest.mark.asyncio
async def test_execute_script_encodes_godot_types(editor: Any) -> None:
    """Returned Vectors and objects use the value codec."""
    payload = await editor.ok("godot_execute_script", {"code": "return Vector2(3, 4)"})
    assert payload["result"] == {"__gd": "Vector2", "x": 3.0, "y": 4.0}

    payload = await editor.ok("godot_execute_script", {"code": 'return get_node("../Player")'})
    assert payload["result"]["__gd"] == "Object"
    assert payload["result"]["class"] == "CharacterBody2D"
    assert payload["result"]["path"] == "Player"


@pytest.mark.asyncio
async def test_selection_round_trip(editor: Any) -> None:
    """``editor.set_selection`` is reflected by ``editor.get_selection``."""
    try:
        await editor.ok("godot_set_selection", {"paths": ["Player"], "inspect": False})
        payload = await editor.ok("get_selected_nodes")
        paths = [node["path"] for node in payload["nodes"]]
        assert paths == ["Player"]
    finally:
        await editor.call("godot_set_selection", {"paths": [], "inspect": False})


@pytest.mark.asyncio
async def test_undo_history_describes_the_last_action(editor: Any) -> None:
    """``editor.undo_history`` names the most recent action."""
    await editor.new_node("Node2D")
    payload = await editor.ok("godot_get_undo_history")

    assert payload["actions"], "the undo history must not be empty after a write"
    assert payload["actions"][0]["name"].startswith(("MCP:", "Create", "Add"))
    assert isinstance(payload["actions"][0]["mergeable"], bool)


@pytest.mark.asyncio
async def test_project_settings_are_readable(editor: Any) -> None:
    """``editor.project_settings`` exposes the project configuration."""
    payload = await editor.ok("godot_get_project_settings", {"prefix": "application/config"})
    settings = payload["settings"]

    assert settings["application/config/name"]
    assert all(key.startswith("application/config") for key in settings)


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wrong_token_is_refused_with_1011_and_close_4001(editor: Any) -> None:
    """PROTOCOL.md §2: wrong token -> error 1011 then close code 4001."""
    handshake = read_handshake()
    assert handshake, "the handshake file is required for this test"

    connection = await raw_ws(int(handshake["port"]))
    try:
        await connection.send(
            json.dumps({"jsonrpc": "2.0", "id": "qa-auth", "method": "auth", "params": {"token": "0" * 48}})
        )
        raw = await asyncio.wait_for(connection.recv(), timeout=5)
        frame = json.loads(raw)
        assert frame["error"]["code"] == 1011
        assert frame["error"]["hint"]
        with pytest.raises(websockets.exceptions.ConnectionClosed) as closed:
            await asyncio.wait_for(connection.recv(), timeout=5)
        assert closed.value.rcvd is not None
        assert closed.value.rcvd.code == 4001, f"unexpected close code: {closed.value.rcvd}"
    finally:
        await connection.close()

    # The editor keeps serving authenticated clients.
    assert (await editor.ok("godot_ping"))["pong"] is True


@pytest.mark.asyncio
async def test_preauth_ping_is_allowed(editor: Any) -> None:
    """Discovery relies on an unauthenticated ``ping`` (PROTOCOL.md §1)."""
    handshake = read_handshake()
    connection = await raw_ws(int(handshake["port"]))
    try:
        await connection.send(json.dumps({"jsonrpc": "2.0", "id": "p1", "method": "ping", "params": {}}))
        frame = json.loads(await asyncio.wait_for(connection.recv(), timeout=5))
        assert frame["result"]["pong"] is True
        assert frame["result"]["requires_auth"] is True
    finally:
        await connection.close()


# --------------------------------------------------------------------------- #
# runtime group (needs a game process)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_runtime_status_reports_the_game_process(editor: Any, game_process: Any) -> None:
    """``runtime.status`` finds the running game."""
    payload = await editor.ok("godot_get_runtime_status")

    assert payload["connected"] is True, "the game must be reachable through the runtime bridge"
    assert "running" in payload and "scene" in payload


@pytest.mark.asyncio
async def test_runtime_get_tree(editor: Any, game_process: Any) -> None:
    """``runtime.get_tree`` returns the live game tree, not the editor one."""
    payload = await editor.ok("godot_get_runtime_tree", {"depth": 3})

    root = payload["root"]
    assert root["name"] == "root"
    assert root["type"] == "Window"
    names = json.dumps(root)
    assert "Main" in names
    assert "Player" in names


@pytest.mark.asyncio
async def test_runtime_eval_returns_a_value(editor: Any, game_process: Any) -> None:
    """``runtime.eval`` runs code inside the running game."""
    payload = await editor.ok("godot_run_gdscript_in_game", {"code": "return 6 * 7"})
    assert payload["result"] == 42


@pytest.mark.asyncio
async def test_runtime_logs(editor: Any, game_process: Any) -> None:
    """``runtime.logs`` returns the captured game output."""
    payload = await editor.ok("godot_get_game_logs", {"limit": 20})

    assert isinstance(payload["entries"], list)
    assert isinstance(payload["next"], int)
    for entry in payload["entries"]:
        assert set(entry) >= {"seq", "ts", "level", "text"}


@pytest.mark.asyncio
async def test_runtime_screenshot_is_1008_in_headless(editor: Any, game_process: Any) -> None:
    """Headless games cannot be captured: 1008, never a crash."""
    payload = await editor.call("godot_take_screenshot", {"max_size": 320})

    assert payload["ok"] is False
    assert payload["error"]["code"] == 1008, f"expected 1008, got {payload}"
    assert payload["error"]["hint"]
    # The bridge must still be healthy afterwards.
    assert (await editor.ok("godot_ping"))["pong"] is True


@pytest.mark.asyncio
async def test_runtime_input_is_sent(editor: Any, game_process: Any) -> None:
    """``runtime.input`` accepts the documented event shapes."""
    payload = await editor.ok(
        "godot_send_input",
        {
            "events": [
                {"type": "key", "keycode": "SPACE", "pressed": True},
                {"type": "key", "keycode": "SPACE", "pressed": False},
                {"type": "motion", "x": 10, "y": 10},
                {"type": "wait", "ms": 10},
            ]
        },
    )
    assert payload["sent"] is True


# --------------------------------------------------------------------------- #
# state hygiene
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scene_on_disk_is_untouched(editor: Any) -> None:
    """Nothing in this suite saves the scene, so ``main.tscn`` is byte-identical."""
    scene = PROJECT_DIR / "main.tscn"
    golden = FIXTURE_PROJECT / "main.tscn"
    if not scene.is_file() or not golden.is_file():
        pytest.skip("no project scene on disk")

    payload = await editor.ok("godot_get_current_scene")
    assert payload["path"] == "res://main.tscn"
    assert scene.read_text(encoding="utf-8") == golden.read_text(encoding="utf-8"), (
        "a test saved the scene; main.tscn must stay pristine"
    )
    assert (await editor.ok("godot_get_scene_tree", {"depth": 1}))["root"]["name"] == "Main"


# --------------------------------------------------------------------------- #
# port conflict (destructive: restarts Godot, excluded from the default run)
# --------------------------------------------------------------------------- #


@pytest.mark.port_conflict
@pytest.mark.asyncio
async def test_editor_moves_to_the_next_port_when_8765_is_busy(fixture_project: Path) -> None:
    """PROTOCOL.md §1: a busy port shifts the editor to 8766/8767 + new handshake."""
    godot = GODOT_BIN
    if not godot.is_file():
        pytest.skip(f"GODOT_BIN not found at {godot}")

    dummy_sockets = []
    launcher = None

    def stop_all_godot() -> None:
        # Anchored pattern: never match an unrelated shell whose command line
        # merely mentions the Godot binary.
        subprocess.run(["pkill", "-f", f"^{godot}"], check=False)
        time.sleep(1.5)

    def launch(project: Path, log_name: str) -> subprocess.Popen:
        log = open(f"/tmp/{log_name}", "wb")
        return subprocess.Popen(
            [str(godot), "--headless", "--path", str(project), "--editor", "res://main.tscn"],
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    try:
        stop_all_godot()
        # Occupy 8765 and 8766 so the plugin has to move up the range.
        for port in (8765, 8766):
            dummy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            dummy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            dummy.bind(("127.0.0.1", port))
            dummy.listen(8)
            dummy_sockets.append(dummy)

        launcher = launch(fixture_project, "qa-portconflict.log")

        handshake_path = fixture_project / ".godot" / "mcp_bridge.json"
        deadline = time.time() + 90
        data: Dict[str, Any] = {}
        while time.time() < deadline:
            try:
                data = json.loads(handshake_path.read_text(encoding="utf-8"))
                if data.get("port"):
                    break
            except (OSError, ValueError):
                pass
            await asyncio.sleep(0.5)

        assert data, "no handshake file was written (log: /tmp/qa-portconflict.log)"
        assert data["schema"] == 1
        assert data["protocol"] == "2.0"
        assert len(data["token"]) == 48
        assert data["port"] != 8765, "the plugin bound the busy port"
        assert 8766 <= data["port"] <= 8784, f"unexpected fallback port {data['port']}"
        assert data["runtime_port"] != data["port"]
        assert tcp_reachable("127.0.0.1", int(data["port"])), "the new port does not answer"
        print(f"[qa] fallback port={data['port']} runtime_port={data['runtime_port']}")

        # The moved editor must authenticate on the new port.
        connection = await raw_ws(int(data["port"]))
        try:
            await connection.send(
                json.dumps({"jsonrpc": "2.0", "id": "0", "method": "auth", "params": {"token": data["token"]}})
            )
            frame = json.loads(await asyncio.wait_for(connection.recv(), timeout=5))
            assert frame["result"]["ok"] is True
        finally:
            await connection.close()
    finally:
        for dummy in dummy_sockets:
            dummy.close()
        if launcher is not None and launcher.poll() is None:
            launcher.terminate()
            try:
                launcher.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                launcher.kill()
        stop_all_godot()
        # Restore the environment the other integration tests expect.
        if (PROJECT_DIR / "project.godot").is_file():
            launch(PROJECT_DIR, "qa-editor-restored.log")
            deadline = time.time() + 90
            while time.time() < deadline:
                handshake = read_handshake()
                if handshake and tcp_reachable("127.0.0.1", int(handshake["port"])):
                    break
                await asyncio.sleep(0.5)
