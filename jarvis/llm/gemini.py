"""Gemini implementation of :class:`~jarvis.llm.base.LLMProvider`."""

from __future__ import annotations

import json
import logging
from typing import Any

from jarvis.llm.base import (
    LLMError,
    LLMProvider,
    LLMResponse,
    Message,
    Role,
    ToolCall,
    ToolDeclaration,
)

logger = logging.getLogger(__name__)

_JSON_TO_GEMINI_TYPE = {
    "object": "OBJECT",
    "array": "ARRAY",
    "string": "STRING",
    "number": "NUMBER",
    "integer": "INTEGER",
    "boolean": "BOOLEAN",
}


def _to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert a JSON schema into the uppercase-typed dialect Gemini expects."""
    converted: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "type" and isinstance(value, str):
            converted[key] = _JSON_TO_GEMINI_TYPE.get(value.lower(), value.upper())
        elif key == "properties" and isinstance(value, dict):
            converted[key] = {k: _to_gemini_schema(v) for k, v in value.items()}
        elif key == "items" and isinstance(value, dict):
            converted[key] = _to_gemini_schema(value)
        elif key in ("enum", "required", "description", "nullable", "format"):
            converted[key] = value
    return converted


class GeminiProvider(LLMProvider):
    """Thin adapter over ``google-genai``. Automatic function calling is OFF:
    Jarvis executes tools itself behind the permission layer."""

    name = "gemini"

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash") -> None:
        self._api_key = api_key
        self._model = model
        self._client: Any = None

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _get_client(self) -> Any:
        if self._client is None:
            if not self.is_configured():
                raise LLMError("GEMINI_API_KEY is not set")
            try:
                from google import genai
            except ImportError as exc:  # pragma: no cover - dependency guard
                raise LLMError("google-genai is not installed") from exc
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    async def complete(
        self,
        messages: list[Message],
        *,
        system_prompt: str,
        tools: list[ToolDeclaration] | None = None,
    ) -> LLMResponse:
        from google.genai import types

        client = self._get_client()
        config_kwargs: dict[str, Any] = {
            "system_instruction": system_prompt,
            "automatic_function_calling": types.AutomaticFunctionCallingConfig(
                disable=True
            ),
            "temperature": 0.2,
        }
        if tools:
            config_kwargs["tools"] = [
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=decl["name"],
                            description=decl["description"],
                            parameters=_to_gemini_schema(decl["parameters"]) or None,
                        )
                        for decl in tools
                    ]
                )
            ]
        try:
            response = await client.aio.models.generate_content(
                model=self._model,
                contents=[_to_content(m, types) for m in messages],
                config=types.GenerateContentConfig(**config_kwargs),
            )
        except Exception as exc:
            logger.exception("gemini request failed")
            raise LLMError(f"Gemini request failed: {exc}") from exc

        return _parse_response(response, self._model)


def _to_content(message: Message, types: Any) -> Any:
    """Map an internal message onto a Gemini ``Content`` object."""
    if message.role is Role.TOOL:
        return types.Content(
            role="user",
            parts=[
                types.Part.from_function_response(
                    name=message.tool_name or "tool",
                    response=_as_response_dict(message.content),
                )
            ],
        )
    if message.role is Role.ASSISTANT:
        parts = []
        if message.content:
            parts.append(types.Part.from_text(text=message.content))
        for call in message.tool_calls:
            parts.append(
                types.Part.from_function_call(name=call.name, args=call.args or {})
            )
        if not parts:
            parts.append(types.Part.from_text(text=" "))
        return types.Content(role="model", parts=parts)
    return types.Content(
        role="user", parts=[types.Part.from_text(text=message.content or " ")]
    )


def _as_response_dict(content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return {"output": content}
    return parsed if isinstance(parsed, dict) else {"output": parsed}


def _parse_response(response: Any, model: str) -> LLMResponse:
    text_chunks: list[str] = []
    tool_calls: list[ToolCall] = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            if getattr(part, "text", None):
                text_chunks.append(part.text)
            call = getattr(part, "function_call", None)
            if call is not None and getattr(call, "name", None):
                tool_calls.append(
                    ToolCall(name=call.name, args=dict(call.args or {}))
                )
    usage = getattr(response, "usage_metadata", None)
    return LLMResponse(
        text="".join(text_chunks).strip(),
        tool_calls=tool_calls,
        model=model,
        raw_usage={
            "prompt_tokens": getattr(usage, "prompt_token_count", None),
            "output_tokens": getattr(usage, "candidates_token_count", None),
        }
        if usage
        else {},
    )
