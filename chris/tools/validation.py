"""Argument validation against a JSON-schema-like subset.

Deterministic and dependency-free (the project's runtime dependency is only
`requests`). Handles: missing required args, wrong types, unexpected arguments,
and invalid enum values. Malformed JSON is caught earlier (the adapter keeps a
non-dict `arguments`), and this layer rejects it structurally.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)


def _matches_type(value: Any, type_name: str) -> bool:
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "null":
        return value is None
    return True


def validate_arguments(
    schema: dict[str, Any] | None, arguments: Any
) -> ValidationResult:
    if not isinstance(arguments, dict):
        return ValidationResult(False, ["arguments must be a JSON object"])
    if not schema:
        return ValidationResult(True)

    errors: list[str] = []
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    allow_extra = schema.get("additionalProperties", True)

    for name in required:
        if name not in arguments:
            errors.append(f"missing required argument: {name}")

    for name, value in arguments.items():
        spec = properties.get(name)
        if spec is None:
            if allow_extra is False:
                errors.append(f"unexpected argument: {name}")
            continue
        expected = spec.get("type")
        if expected and not _matches_type(value, expected):
            errors.append(f"argument {name!r} must be {expected}")
        enum = spec.get("enum")
        if enum is not None and value not in enum:
            errors.append(f"argument {name!r} must be one of {enum}")

    return ValidationResult(valid=not errors, errors=errors)
