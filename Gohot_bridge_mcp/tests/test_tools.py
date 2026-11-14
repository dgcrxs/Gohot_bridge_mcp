"""Tests for the MCP tool surface: registration, schemas, docs and error shaping.

The registration checks need no Godot process. The error-shaping checks run
against :class:`tests.fake_godot.FakeGodot`.
"""

import json
from typing import Any, Dict, List

import pytest
import pytest_asyncio

from server.errors import (
    AUTH_FAILED,
    BRIDGE_ERROR,
    ERROR_NAMES,
    INTERNAL_ERROR,
    TIMEOUT,
    GodotError,
    error_payload,
    internal_error,
)

from tests.conftest import FIXTURE_PROJECT, McpHandle, free_port, mcp_session

LEGACY_TOOLS = {
    "get_scene_tree",
    "get_node_properties",
    "get_selected_nodes",
    "get_editor_info",
    "add_node",
    "set_node_property",
    "execute_script",
}

# Tools whose failure mode is the point of the tool (they report ok:false by design).
NON_FAILING_TOOLS = {"godot_check_script"}


async def list_tools(client: Any) -> List[Any]:
    """All tools advertised by the server."""
    return list((await client.list_tools()).tools)


@pytest_asyncio.fixture
async def tool_cache(mcp_fake: McpHandle) -> Dict[str, Any]:
    """Snapshot of the advertised tools, keyed by name."""
    tools = await list_tools(mcp_fake.client)
    return {tool.name: tool for tool in tools}


# --------------------------------------------------------------------------- #
# registration
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_tool_count_is_at_least_71(tool_cache: Dict[str, Any]) -> None:
    """The v2 surface must not regress below the documented 71 tools."""
    assert len(tool_cache) >= 71, f"only {len(tool_cache)} tools registered"


@pytest.mark.asyncio
async def test_tool_names_are_unique(tool_cache: Dict[str, Any]) -> None:
    """Duplicate names would make one tool silently shadow another."""
    names = [tool.name for tool in tool_cache.values()]
    assert len(names) == len(set(names))


@pytest.mark.asyncio
async def test_legacy_v1_tools_are_kept(tool_cache: Dict[str, Any]) -> None:
    """PROTOCOL.md §3.11: the seven v1 names keep working."""
    missing = LEGACY_TOOLS - set(tool_cache)
    assert not missing, f"legacy tools disappeared: {sorted(missing)}"


@pytest.mark.asyncio
async def test_v2_tools_use_the_godot_prefix(tool_cache: Dict[str, Any]) -> None:
    """PROTOCOL.md §7: v2 tools are named ``godot_<group>_<action>``."""
    unprefixed = {name for name in tool_cache if not name.startswith("godot_")} - LEGACY_TOOLS
    assert not unprefixed, f"tools without the godot_ prefix: {sorted(unprefixed)}"

    single_verb = {"godot_ping", "godot_undo", "godot_redo"}
    for name in tool_cache:
        if name.startswith("godot_") and name not in single_verb:
            assert "_" in name[6:], f"{name} does not follow godot_<group>_<action>"


@pytest.mark.asyncio
async def test_expected_groups_are_covered(tool_cache: Dict[str, Any]) -> None:
    """Each PROTOCOL.md method group needs at least one tool."""
    expected = {
        "godot_ping",
        "godot_get_scene_tree",
        "godot_get_node",
        "godot_list_properties",
        "godot_read_script",
        "godot_list_resources",
        "godot_run_scene",
        "godot_get_class_info",
        "godot_undo",
        "godot_execute_script",
    }
    missing = expected - set(tool_cache)
    assert not missing, f"missing tools for documented groups: {sorted(missing)}"


# --------------------------------------------------------------------------- #
# docs and schemas
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_every_tool_documents_its_arguments(tool_cache: Dict[str, Any]) -> None:
    """An MCP client (and the model behind it) needs an ``Args:`` section."""
    offenders = []
    for name, tool in tool_cache.items():
        description = tool.description or ""
        if "Args:" not in description:
            offenders.append(name)
    assert not offenders, f"tools without an 'Args:' docstring section: {sorted(offenders)}"


@pytest.mark.asyncio
async def test_every_tool_exposes_a_valid_schema(tool_cache: Dict[str, Any]) -> None:
    """``input_schema`` is a JSON-Schema object and required keys exist."""
    for name, tool in tool_cache.items():
        schema = tool.input_schema
        assert isinstance(schema, dict), f"{name} has no input schema"
        assert schema.get("type") == "object", f"{name} schema is not an object schema"
        properties = schema.get("properties", {})
        assert isinstance(properties, dict)
        for required in schema.get("required", []):
            assert required in properties, f"{name} requires '{required}' but does not declare it"


@pytest.mark.asyncio
async def test_annotation_hints_match_the_tool_semantics(tool_cache: Dict[str, Any]) -> None:
    """Read-only / destructive hints must be present and coherent."""
    for name, tool in tool_cache.items():
        annotations = tool.annotations
        assert annotations is not None, f"{name} has no annotations"
        assert annotations.open_world_hint is False, f"{name} must be closed-world"
        if annotations.read_only_hint is True:
            assert annotations.destructive_hint is False, f"{name} is read-only yet destructive"
    assert tool_cache["godot_delete_node"].annotations.destructive_hint is True
    assert tool_cache["godot_ping"].annotations.read_only_hint is True
    assert tool_cache["godot_undo"].annotations.read_only_hint is False


@pytest.mark.asyncio
async def test_legacy_add_node_keeps_its_v1_parameters(tool_cache: Dict[str, Any]) -> None:
    """v1 clients call ``add_node(node_type, node_name, parent_path)``."""
    schema = tool_cache["add_node"].input_schema
    assert set(schema["properties"]) == {"node_type", "node_name", "parent_path"}
    assert schema["required"] == ["node_type"]


@pytest.mark.asyncio
async def test_legacy_set_node_property_keeps_its_v1_parameters(tool_cache: Dict[str, Any]) -> None:
    """v1 clients call ``set_node_property(node_path, property, value)``."""
    schema = tool_cache["set_node_property"].input_schema
    assert set(schema["properties"]) == {"node_path", "property", "value"}
    assert set(schema["required"]) == {"node_path", "property", "value"}


# --------------------------------------------------------------------------- #
# SDK-level argument validation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_unknown_tool_is_refused(mcp_fake: McpHandle) -> None:
    """A typo in the tool name must not be swallowed."""
    result = await mcp_fake.client.call_tool("godot_there_is_no_such_tool", {})
    text = "".join(chunk.text or "" for chunk in (result.content or []))
    assert result.is_error is True
    assert "unknown tool" in text.lower()


@pytest.mark.asyncio
async def test_missing_required_argument_is_refused(mcp_fake: McpHandle) -> None:
    """Required parameters are enforced before the RPC is attempted."""
    result = await mcp_fake.client.call_tool("godot_get_property", {"path": "Player"})
    text = "".join(chunk.text or "" for chunk in (result.content or []))
    assert result.is_error is True
    assert "property" in text and "required" in text.lower()


@pytest.mark.asyncio
async def test_unknown_argument_is_rejected(mcp_fake: McpHandle) -> None:
    """Unknown keys must be rejected, otherwise typos silently no-op."""
    result = await mcp_fake.client.call_tool("godot_get_node", {"path": "Player", "with_prop": True})
    text = "".join(chunk.text or "" for chunk in (result.content or []))
    assert result.is_error is True, f"unknown argument 'with_prop' was accepted (payload: {text[:200]})"


# --------------------------------------------------------------------------- #
# error shaping (PROTOCOL.md §5)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_tool_error_payload_is_structured(mcp_fake: McpHandle) -> None:
    """A failing RPC becomes ``ok:false`` with code/message/hint/context."""
    mcp_fake.fake.error_responses["node.get"] = {
        "code": 1002,
        "message": "node not found: 'Ghost'",
        "hint": "Call godot_get_scene_tree to list valid node paths.",
        "context": {"path": "Ghost"},
    }
    payload = await mcp_fake.call("godot_get_node", {"path": "Ghost"})

    assert payload["ok"] is False
    error = payload["error"]
    assert error["code"] == 1002
    assert error["hint"]
    assert error["context"] == {"path": "Ghost"}
    assert mcp_fake.fake.count("node.get") == 1


@pytest.mark.asyncio
async def test_tool_error_has_no_traceback(mcp_fake: McpHandle) -> None:
    """Internal failures must not leak Python stack traces to the agent."""
    mcp_fake.fake.error_responses["node.get"] = {
        "code": -32603,
        "message": "internal failure",
        "hint": "Retry.",
        "context": {},
    }
    payload = await mcp_fake.call("godot_get_node", {"path": "Ghost"})
    text = json.dumps(payload)

    assert payload["ok"] is False
    assert "Traceback" not in text
    assert 'File "' not in text
    assert "site-packages" not in text


@pytest.mark.asyncio
async def test_ok_tools_carry_an_ok_flag(mcp_fake: McpHandle) -> None:
    """Every successful tool answer sets ``ok: true`` explicitly."""
    payload = await mcp_fake.ok("godot_ping")
    assert payload["pong"] is True
    assert payload["protocol"] == "2.0"


@pytest.mark.asyncio
async def test_get_config_merges_local_bridge_state(mcp_fake: McpHandle) -> None:
    """``godot_get_config`` reports the resolved local connection too."""
    payload = await mcp_fake.ok("godot_get_config")

    assert payload["local"]["uri"].startswith("ws://127.0.0.1:")
    assert "env" in payload["local"]["source"]
    assert payload["local"]["project_path"]


@pytest.mark.asyncio
async def test_list_methods_reports_capabilities(mcp_fake: McpHandle) -> None:
    """``godot_list_methods`` mirrors ``get_capabilities``."""
    payload = await mcp_fake.ok("godot_list_methods")
    assert payload["count"] == len(payload["methods"])
    assert "ping" in payload["methods"]
    assert payload["groups"]


# --------------------------------------------------------------------------- #
# resilience
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def dead_bridge_client(monkeypatch: pytest.MonkeyPatch) -> Any:
    """An MCP client whose bridge points at a port nobody listens on."""
    from server import config as config_module
    from server.bridge import reset_bridge

    monkeypatch.setenv("GODOT_MCP_PORT", str(free_port()))
    monkeypatch.setenv("GODOT_MCP_TOKEN", "dead-token")
    monkeypatch.setenv("GODOT_MCP_PROJECT", str(FIXTURE_PROJECT))
    config_module.invalidate_cache()
    await reset_bridge()

    async with mcp_session() as handle:
        yield handle

    config_module.invalidate_cache()
    await reset_bridge()


@pytest.mark.asyncio
async def test_tool_failure_without_an_editor_is_still_structured(dead_bridge_client: McpHandle) -> None:
    """A dead editor must produce ok:false, never an exception or a traceback."""
    payload = await dead_bridge_client.call("godot_ping")
    text = json.dumps(payload)

    assert payload["ok"] is False
    assert payload["error"]["code"] in {TIMEOUT, BRIDGE_ERROR, INTERNAL_ERROR}
    assert payload["error"]["message"]
    assert "Traceback" not in text


@pytest.mark.asyncio
async def test_server_info_never_fails(dead_bridge_client: McpHandle) -> None:
    """``godot_server_info`` degrades per section instead of raising."""
    payload = await dead_bridge_client.ok("godot_server_info")

    assert payload["server"]["version"]
    assert payload["server"]["protocol"] == "2.0"
    assert payload["bridge"]["uri"].startswith("ws://127.0.0.1:")
    assert payload["editor"] is None
    assert payload["capabilities"] is None
    assert payload["errors"]["editor"]["code"] in {TIMEOUT, BRIDGE_ERROR, INTERNAL_ERROR}


# --------------------------------------------------------------------------- #
# error payload helpers
# --------------------------------------------------------------------------- #


def test_error_payload_never_leaks_a_traceback() -> None:
    """``internal_error`` keeps the exception type and message only."""

    class Boom(Exception):
        pass

    error = internal_error(Boom("kaboom"))
    assert error.code == BRIDGE_ERROR
    assert "Boom" in error.message and "kaboom" in error.message
    assert "Traceback" not in json.dumps(error.to_dict())

    payload = error_payload(ValueError("plain"))
    assert payload["ok"] is False
    assert payload["error"]["code"] == BRIDGE_ERROR


def test_error_names_cover_every_documented_code() -> None:
    """PROTOCOL.md §5 codes must all be named (used in logs and tests)."""
    documented = {
        -32700,
        -32600,
        -32601,
        -32602,
        -32603,
        1001,
        1002,
        1003,
        1004,
        1005,
        1006,
        1007,
        1008,
        1009,
        1010,
        1011,
        1012,
        1013,
        1014,
    }
    missing = documented - set(ERROR_NAMES)
    assert not missing, f"unnamed error codes: {sorted(missing)}"
    assert GodotError(AUTH_FAILED, "x").name == "auth_failed"
