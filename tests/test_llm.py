from __future__ import annotations

import pytest

from jarvis.llm.base import LLMError, Message, ToolCall
from jarvis.llm.gemini import GeminiProvider, _parse_response, _to_gemini_schema

types = pytest.importorskip("google.genai.types")


def test_schema_types_are_uppercased() -> None:
    converted = _to_gemini_schema(
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "n"},
                "names": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["limit"],
            "title": "dropped",
        }
    )
    assert converted["type"] == "OBJECT"
    assert converted["properties"]["limit"]["type"] == "INTEGER"
    assert converted["properties"]["names"]["items"]["type"] == "STRING"
    assert converted["required"] == ["limit"]
    assert "title" not in converted


def test_every_registered_tool_builds_a_valid_gemini_declaration() -> None:
    from jarvis.tools import REGISTRY

    for declaration in REGISTRY.declarations():
        types.FunctionDeclaration(
            name=declaration["name"],
            description=declaration["description"],
            parameters=_to_gemini_schema(declaration["parameters"]) or None,
        )


def test_messages_map_onto_gemini_contents() -> None:
    from jarvis.llm.gemini import _to_content

    user = _to_content(Message.user("hello"), types)
    assert user.role == "user" and user.parts[0].text == "hello"

    assistant = _to_content(
        Message.assistant("thinking", [ToolCall(name="cpu_info", args={})]), types
    )
    assert assistant.role == "model"
    assert assistant.parts[-1].function_call.name == "cpu_info"

    tool_turn = _to_content(Message.tool("cpu_info", '{"ok": true}'), types)
    assert tool_turn.parts[0].function_response.response == {"ok": True}


def test_non_json_tool_output_is_wrapped() -> None:
    from jarvis.llm.gemini import _to_content

    content = _to_content(Message.tool("t", "plain text"), types)
    assert content.parts[0].function_response.response == {"output": "plain text"}


def test_parse_response_extracts_text_and_calls() -> None:
    response = types.GenerateContentResponse(
        candidates=[
            {
                "content": {
                    "parts": [
                        {"text": "here you go"},
                        {"function_call": {"name": "cpu_info", "args": {"x": 1}}},
                    ]
                }
            }
        ]
    )
    parsed = _parse_response(response, "gemini-2.0-flash")
    assert parsed.text == "here you go"
    assert parsed.tool_calls[0].name == "cpu_info"
    assert parsed.tool_calls[0].args == {"x": 1}
    assert parsed.wants_tools


def test_provider_without_key_is_unconfigured() -> None:
    provider = GeminiProvider(api_key="")
    assert not provider.is_configured()
    with pytest.raises(LLMError):
        provider._get_client()
