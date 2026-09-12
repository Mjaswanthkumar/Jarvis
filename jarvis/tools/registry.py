"""Tool registry: declaration, JSON-schema generation and safe execution.

Tools are plain typed Python functions. The LLM only ever selects a tool *name*
plus JSON arguments; it never executes anything itself.
"""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from jarvis.tools.permissions import PermissionLevel

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

#: JSON-schema keys Gemini's function-declaration parser rejects.
_UNSUPPORTED_SCHEMA_KEYS = {
    "title",
    "additionalProperties",
    "$schema",
    "definitions",
    "$defs",
    "exclusiveMinimum",
    "exclusiveMaximum",
}


class ToolResult(BaseModel):
    """Uniform envelope returned by every tool invocation."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    tool: str
    data: Any = None
    error: str | None = None
    duration_ms: int = 0
    #: Set when the tool needs explicit user confirmation before running.
    needs_confirmation: bool = False


@dataclass(slots=True)
class ToolSpec:
    """A registered tool and everything needed to describe it to an LLM."""

    name: str
    description: str
    permission: PermissionLevel
    func: Callable[..., Any]
    args_model: type[BaseModel]
    tags: tuple[str, ...] = field(default_factory=tuple)
    #: True when the result can contain text written by someone other than the
    #: user -- file contents, logs, commit messages, window titles. Those are
    #: fenced before they reach the model (see jarvis.injection).
    untrusted_output: bool = False

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return _sanitize_schema(self.args_model.model_json_schema())

    def declaration(self) -> dict[str, Any]:
        """OpenAPI-ish declaration understood by the LLM providers."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters_schema,
        }


def _sanitize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Strip JSON-schema constructs the Gemini function API does not accept.

    Notably ``anyOf`` produced by ``X | None`` is collapsed to the non-null
    branch, and optional fields simply drop out of ``required``.
    """
    cleaned: dict[str, Any] = {}
    for key, value in schema.items():
        if key in _UNSUPPORTED_SCHEMA_KEYS:
            continue
        if key == "anyOf":
            branches = [b for b in value if b.get("type") != "null"]
            chosen = branches[0] if branches else {"type": "string"}
            cleaned.update(_sanitize_schema(chosen))
            continue
        if isinstance(value, dict):
            cleaned[key] = _sanitize_schema(value)
        elif isinstance(value, list):
            cleaned[key] = [
                _sanitize_schema(v) if isinstance(v, dict) else v for v in value
            ]
        else:
            cleaned[key] = value
    if "properties" in cleaned:
        cleaned.setdefault("type", "object")
    return cleaned


def _build_args_model(name: str, func: Callable[..., Any]) -> type[BaseModel]:
    """Derive a pydantic model for a tool's keyword arguments."""
    signature = inspect.signature(func)
    hints = inspect.get_annotations(func, eval_str=True)
    fields: dict[str, Any] = {}
    for param_name, param in signature.parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        annotation = hints.get(param_name, str)
        default = ... if param.default is inspect.Parameter.empty else param.default
        fields[param_name] = (annotation, default)
    model_name = "".join(part.capitalize() for part in name.split("_")) + "Args"
    return create_model(model_name, __config__=ConfigDict(extra="forbid"), **fields)


class ToolRegistry:
    """Holds every known tool and executes them with validation + timing."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"duplicate tool name: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def all(self) -> list[ToolSpec]:
        return sorted(self._tools.values(), key=lambda s: s.name)

    def available(self) -> list[ToolSpec]:
        """Tools the agent may see -- BLOCKED tools are never advertised."""
        return [s for s in self.all() if not s.permission.is_blocked()]

    def declarations(self) -> list[dict[str, Any]]:
        return [spec.declaration() for spec in self.available()]

    def execute(self, name: str, args: dict[str, Any] | None = None) -> ToolResult:
        """Validate arguments and run a tool, never raising to the caller."""
        started = time.perf_counter()
        spec = self.get(name)
        if spec is None:
            return ToolResult(ok=False, tool=name, error=f"unknown tool: {name}")
        if spec.permission.is_blocked():
            return ToolResult(
                ok=False, tool=name, error=f"tool '{name}' is blocked by policy"
            )
        try:
            parsed = spec.args_model(**(args or {}))
        except ValidationError as exc:
            return ToolResult(
                ok=False,
                tool=name,
                error=f"invalid arguments: {exc.errors(include_url=False)}",
            )
        try:
            data = spec.func(**parsed.model_dump())
            ok, error = True, None
        except Exception as exc:  # tools must never crash the agent loop
            logger.exception("tool %s failed", name)
            data, ok, error = None, False, f"{type(exc).__name__}: {exc}"
        return ToolResult(
            ok=ok,
            tool=name,
            data=data,
            error=error,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


REGISTRY = ToolRegistry()


def tool(
    *,
    name: str | None = None,
    description: str,
    permission: PermissionLevel,
    tags: tuple[str, ...] = (),
    untrusted_output: bool = False,
    registry: ToolRegistry | None = None,
) -> Callable[[F], F]:
    """Decorator registering a typed function as an agent tool."""

    def decorator(func: F) -> F:
        tool_name = name or func.__name__
        spec = ToolSpec(
            name=tool_name,
            description=description.strip(),
            permission=permission,
            func=func,
            args_model=_build_args_model(tool_name, func),
            tags=tags,
            untrusted_output=untrusted_output,
        )
        (registry or REGISTRY).register(spec)
        return func

    return decorator


__all__ = [
    "REGISTRY",
    "Field",
    "PermissionLevel",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "tool",
]
