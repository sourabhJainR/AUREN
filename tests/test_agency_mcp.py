from portable.agency_agent_capabilities import AllowlistedToolPolicy, ToolRegistry, ToolSpec
from portable.agency_mcp import MCPRequest, MCPToolEndpoint


def test_mcp_list_and_call():
    registry = ToolRegistry([ToolSpec(
        "echo", "return text", "execution", handler=lambda text: text,
        is_extension=True,
        extension_metadata={
            "available": True, "manifest_valid": True, "trust_class": "trusted",
            "requires_approval": False, "permissions": [], "artifact_fresh": True,
            "output_limit_chars": 100,
        },
        extension_trust_granted=True,
        extension_artifact_fresh=True,
    )])
    endpoint = MCPToolEndpoint(registry, AllowlistedToolPolicy(allowed_tools=frozenset({"echo"})))
    listed = endpoint.handle(MCPRequest(1, "tools/list", {})).as_dict()
    assert listed["result"]["tools"][0]["name"] == "echo"
    called = endpoint.handle(MCPRequest(2, "tools/call", {"name": "echo", "arguments": {"text": "ok"}})).as_dict()
    assert called["result"]["content"][0]["text"] == "ok"

def test_mcp_extension_policy_denies_before_handler_and_bounds_output():
    calls = []
    metadata = {
        "available": True,
        "manifest_valid": True,
        "trust_class": "trusted",
        "requires_approval": True,
        "permissions": ["read-repository"],
        "artifact_fresh": True,
        "output_limit_chars": 8,
    }
    guarded = ToolSpec(
        "extension-echo", "guarded extension", "execution",
        handler=lambda text: calls.append(text) or ("result:" + text),
        is_extension=True,
        extension_metadata=metadata,
        extension_trust_granted=True,
        extension_approval_granted=False,
        extension_permissions=("read-repository",),
        extension_artifact_fresh=True,
    )
    registry = ToolRegistry([guarded])
    endpoint = MCPToolEndpoint(
        registry, AllowlistedToolPolicy(allowed_tools=frozenset({"extension-echo"}))
    )
    denied = endpoint.handle(
        MCPRequest(10, "tools/call", {"name": "extension-echo", "arguments": {"text": "secret"}})
    ).as_dict()
    assert "error" in denied
    assert calls == []

    approved = ToolSpec(
        **{
            **guarded.__dict__,
            "extension_approval_granted": True,
        }
    )
    endpoint = MCPToolEndpoint(
        ToolRegistry([approved]),
        AllowlistedToolPolicy(allowed_tools=frozenset({"extension-echo"})),
    )
    allowed = endpoint.handle(
        MCPRequest(11, "tools/call", {"name": "extension-echo", "arguments": {"text": "ok"}})
    ).as_dict()
    text = allowed["result"]["content"][0]["text"]
    assert len(text) <= 8
    assert calls == ["ok"]


def test_mcp_extension_with_missing_policy_never_falls_back_to_allowlist():
    calls = []
    registry = ToolRegistry([
        ToolSpec(
            "unconfigured-extension", "must be denied", "execution",
            handler=lambda: calls.append("called") or "unsafe",
            is_extension=True,
        )
    ])
    endpoint = MCPToolEndpoint(
        registry,
        AllowlistedToolPolicy(allowed_tools=frozenset({"unconfigured-extension"})),
    )
    result = endpoint.handle(
        MCPRequest(12, "tools/call", {"name": "unconfigured-extension", "arguments": {}})
    ).as_dict()
    assert "error" in result
    assert calls == []
