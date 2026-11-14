# Godot Bridge MCP — test suite

Independent verification of the v2.0 contract in [`../PROTOCOL.md`](../PROTOCOL.md),
covering the Python server (`server/`), the Godot plugin (`addons/godot_mcp/`) and
the value codec end to end.

## Quick start

```bash
pip install pytest pytest-asyncio

# unit tests only (no Godot needed, ~2 s)
pytest tests/test_config.py tests/test_bridge.py tests/test_tools.py -q

# everything (integration tests self-skip when no editor answers)
GODOT_MCP_PROJECT=/tmp/gdproj pytest -q

# the destructive port-conflict test (kills + restarts the editor)
GODOT_MCP_PROJECT=/tmp/gdproj pytest tests/test_godot_editor.py -q -m port_conflict
```

`pytest.ini` registers two markers and excludes `port_conflict` from the default
run, because that test stops every Godot process on the machine.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `GODOT_MCP_PROJECT` | `/tmp/gdproj` | Project whose `.godot/mcp_bridge.json` is used by integration tests |
| `GODOT_BIN` | `/tmp/Godot_v4.7.2-stable_linux.x86_64` | Godot executable for launch-based tests |

The integration tests expect a headless editor already running on that project:

```bash
killall -q Godot_v4.7.2-stable_linux.x86_64
rm -rf /tmp/gdproj/addons && cp -r addons /tmp/gdproj/
cd /tmp/gdproj && setsid nohup timeout 900 /tmp/Godot_v4.7.2-stable_linux.x86_64 \
    --headless --path /tmp/gdproj --editor res://main.tscn \
    > /tmp/gdproj/qa-editor.log 2>&1 < /dev/null &
```

The runtime tests start a game process themselves when
`runtime.status.connected` is false, and stop it at the end of the session.

## Files

| File | What it verifies |
|---|---|
| `conftest.py` | Fixtures: `editor`, `mcp_fake`, `fake_godot`, `game_process`, session hygiene (restores `main.tscn`) |
| `fake_godot.py` | A configurable fake editor bridge (auth, errors, drops, silence, out-of-order replies) |
| `test_config.py` | PROTOCOL §1: discovery order, handshake validation, port probing, cache TTL/invalidation |
| `test_bridge.py` | PROTOCOL §2/§6: auth, error pass-through, timeouts, retry policy, heartbeat, 20-way concurrency |
| `test_tools.py` | PROTOCOL §7: ≥71 tools, legacy names, `Args:` docs, schemas, annotations, error shaping |
| `test_godot_editor.py` | Real editor: scene/node/prop/undo-redo, scripts, resources, signals, docs, auth, runtime |
| `fixtures/project/` | Minimal Godot project used by the port-conflict test |

## Conventions

* **Self-cleaning** — every integration test creates uniquely named nodes,
  scripts and files through `editor.new_node()` / `new_script()` / `new_file()`;
  the `editor` fixture deletes them in teardown.
* **No scene writes** — nothing calls `godot_save_scene`, so `main.tscn` on disk
  never changes. `pytest_sessionfinish` restores it if something does.
* **Contract-driven** — the write-method set and the timeout table are parsed out
  of `PROTOCOL.md` instead of being duplicated in the tests.
* **No Godot source changes** — failures are reported, never patched in place.

## Known failing tests

These failures are intentional: each one pins a source defect documented in the
QA report (`FAIL` list). Do not "fix" the test — fix the plugin/server.

| Test | Expected by contract | Actual |
|---|---|---|
| `test_color_round_trip` | `{"__gd":"Color","r":..}` round trips | silently becomes `Color(0,0,0,0)` |
| `test_undecodable_value_reports_1004` | 1004 and no mutation | `ok:true`, value silently zeroed |
| `test_absolute_and_relative_paths_are_equivalent` | `/root/Player` resolves | 1002 node not found |
| `test_scene_modified_flag_tracks_unsaved_edits` | `modified:true` after an edit | always `false` |
| `test_connect_failure_maps_to_1010` | `GodotError(1010)` | raw `ConnectionRefusedError` |
| `test_server_info_never_fails` | degrades per section | `ok:false` (same root cause) |
| `test_unknown_argument_is_rejected` | unknown arg rejected | silently ignored |
