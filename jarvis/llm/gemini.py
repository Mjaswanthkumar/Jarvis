"""Gemini implementation of :class:`~jarvis.llm.base.LLMProvider`."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import re
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

#: Transient failures worth retrying, and how hard to try.
_RETRY_STATUSES = ("RESOURCE_EXHAUSTED", "UNAVAILABLE", "429", "503")
_MAX_ATTEMPTS = 3
_MAX_BACKOFF_SECONDS = 30.0
_RETRY_DELAY_PATTERN = re.compile(r"retry in ([0-9.]+)s", re.IGNORECASE)

_NO_SPEECH = "(NO SPEECH)"
_TRANSCRIBE_PROMPT = (
    "Transcribe the spoken words in this audio verbatim. Return only the "
    "transcript, with no commentary, quotes or formatting. If there is no "
    f"intelligible speech, return exactly {_NO_SPEECH}."
)

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

    @property
    def supports_transcription(self) -> bool:
        return self.is_configured()

    async def transcribe(self, audio: bytes, mime_type: str) -> str:
        """Transcribe recorded speech with Gemini's audio input."""
        from google.genai import types

        client = self._get_client()
        try:
            response = await client.aio.models.generate_content(
                model=self._model,
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(text=_TRANSCRIBE_PROMPT),
                            types.Part.from_bytes(data=audio, mime_type=mime_type),
                        ],
                    )
                ],
                config=types.GenerateContentConfig(temperature=0.0),
            )
        except Exception as exc:
            logger.warning("gemini transcription failed: %s", exc)
            raise LLMError(_friendly_error(exc, self._model)) from exc

        text = _parse_response(response, self._model).text
        return "" if text.strip().upper() == _NO_SPEECH else text

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
        contents = [_to_content(m, types) for m in messages]
        config = types.GenerateContentConfig(**config_kwargs)

        last_error: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                response = await client.aio.models.generate_content(
                    model=self._model, contents=contents, config=config
                )
                return _parse_response(response, self._model)
            except Exception as exc:
                last_error = exc
                if attempt == _MAX_ATTEMPTS - 1 or not _is_retryable(exc):
                    break
                delay = _retry_delay(exc, attempt)
                logger.warning(
                    "gemini transient failure (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1,
                    _MAX_ATTEMPTS,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)

        assert last_error is not None
        logger.warning("gemini request failed: %s", last_error)
        raise LLMError(_friendly_error(last_error, self._model)) from last_error


def _is_retryable(exc: Exception) -> bool:
    text = str(exc)
    return any(marker in text for marker in _RETRY_STATUSES)


def _retry_delay(exc: Exception, attempt: int) -> float:
    """Honour the server's own retry hint, else exponential backoff + jitter."""
    match = _RETRY_DELAY_PATTERN.search(str(exc))
    if match:
        return min(float(match.group(1)) + 0.5, _MAX_BACKOFF_SECONDS)
    return min(2.0**attempt + random.uniform(0, 0.5), _MAX_BACKOFF_SECONDS)


def _friendly_error(exc: Exception, model: str) -> str:
    """Turn provider exceptions into something a user can act on."""
    text = str(exc)
    if "PERMISSION_DENIED" in text or "403" in text:
        return (
            f"Gemini denied access to '{model}'. The API key's Google Cloud project "
            "is not allowed to generate content -- create a new key in AI Studio."
        )
    if "NOT_FOUND" in text or "404" in text:
        return f"Gemini model '{model}' does not exist or was retired. Set GEMINI_MODEL to a current model."
    if "RESOURCE_EXHAUSTED" in text or "429" in text:
        return (
            "Gemini rate limit reached for this model. Wait a moment, or set "
            "GEMINI_MODEL to a model with more free-tier headroom."
        )
    if "UNAVAILABLE" in text or "503" in text:
        return f"Gemini model '{model}' is overloaded right now. Try again shortly."
    return f"Gemini request failed: {text}"


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
            part = types.Part.from_function_call(name=call.name, args=call.args or {})
            if call.signature:
                # Gemini 3 rejects a replayed function call without its signature.
                part.thought_signature = base64.b64decode(call.signature)
            parts.append(part)
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
                raw_signature = getattr(part, "thought_signature", None)
                tool_calls.append(
                    ToolCall(
                        name=call.name,
                        args=dict(call.args or {}),
                        signature=(
                            base64.b64encode(raw_signature).decode()
                            if raw_signature
                            else None
                        ),
                    )
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
