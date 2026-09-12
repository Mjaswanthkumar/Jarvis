from __future__ import annotations

import base64

import pytest

from jarvis.llm.base import LLMError, Message, ToolCall
from jarvis.llm.gemini import (
    GeminiProvider,
    _is_retryable,
    _parse_response,
    _retry_delay,
    _to_gemini_schema,
)

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
    parsed = _parse_response(response, "gemini-3.8-flash")
    assert parsed.text == "here you go"
    assert parsed.tool_calls[0].name == "cpu_info"
    assert parsed.tool_calls[0].args == {"x": 1}
    assert parsed.wants_tools


def test_provider_without_key_is_unconfigured() -> None:
    provider = GeminiProvider(api_key="")
    assert not provider.is_configured()
    with pytest.raises(LLMError):
        provider._get_client()


def test_thought_signature_is_captured_from_a_response() -> None:
    """Gemini 3 rejects a replayed function call whose signature was dropped."""
    signature = b"opaque-reasoning-token"
    response = types.GenerateContentResponse(
        candidates=[
            {
                "content": {
                    "parts": [
                        {
                            "function_call": {"name": "cpu_info", "args": {}},
                            "thought_signature": signature,
                        }
                    ]
                }
            }
        ]
    )
    parsed = _parse_response(response, "gemini-3.5-flash-lite")
    assert parsed.tool_calls[0].signature
    assert base64.b64decode(parsed.tool_calls[0].signature) == signature


def test_thought_signature_is_replayed_on_the_model_turn() -> None:
    from jarvis.llm.gemini import _to_content

    signature = b"opaque-reasoning-token"
    call = ToolCall(
        name="cpu_info", args={}, signature=base64.b64encode(signature).decode()
    )
    content = _to_content(Message.assistant("", [call]), types)
    assert content.parts[-1].thought_signature == signature


def test_a_call_without_a_signature_still_round_trips() -> None:
    from jarvis.llm.gemini import _to_content

    content = _to_content(Message.assistant("", [ToolCall(name="cpu_info")]), types)
    assert content.parts[-1].function_call.name == "cpu_info"


@pytest.mark.parametrize(
    "message",
    [
        "429 RESOURCE_EXHAUSTED quota exceeded",
        "503 UNAVAILABLE model overloaded",
    ],
)
def test_transient_failures_are_retryable(message: str) -> None:
    assert _is_retryable(RuntimeError(message))


@pytest.mark.parametrize(
    "message",
    ["403 PERMISSION_DENIED", "404 NOT_FOUND", "400 INVALID_ARGUMENT"],
)
def test_permanent_failures_are_not_retried(message: str) -> None:
    assert not _is_retryable(RuntimeError(message))


def test_server_retry_hint_is_honoured() -> None:
    delay = _retry_delay(RuntimeError("Please retry in 17.26s."), attempt=0)
    assert 17.0 < delay < 18.5


def test_backoff_grows_and_stays_bounded() -> None:
    first = _retry_delay(RuntimeError("429"), attempt=0)
    later = _retry_delay(RuntimeError("429"), attempt=3)
    assert first < later <= 30.0


def test_rate_limit_error_message_is_actionable() -> None:
    from jarvis.llm.gemini import _friendly_error

    text = _friendly_error(RuntimeError("429 RESOURCE_EXHAUSTED"), "m")
    assert "rate limit" in text and "GEMINI_MODEL" in text
