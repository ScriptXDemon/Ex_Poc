"""Pydantic model -> strict JSON Schema (OpenAI-style structured outputs).

Strict mode wants: every object has additionalProperties=false and lists all properties as required; optional
fields become ``anyOf [T, null]``; no $ref (we inline $defs for maximum provider compatibility).
"""

from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel

_DROP = {"title", "default", "examples", "format", "minLength", "maxLength", "minimum", "maximum", "pattern",
         "minItems", "maxItems", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "uniqueItems"}


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})

    def resolve(node: Any, depth: int = 0) -> Any:
        if depth > 40:
            raise ValueError("schema too deep")
        if isinstance(node, list):
            return [resolve(n, depth + 1) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            name = node["$ref"].split("/")[-1]
            target = copy.deepcopy(defs[name])
            extra = {k: v for k, v in node.items() if k != "$ref"}
            target.update(extra)
            return resolve(target, depth + 1)
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k in _DROP:
                continue
            out[k] = resolve(v, depth + 1)
        if out.get("type") == "object" or "properties" in out:
            props = out.get("properties", {})
            out["type"] = "object"
            out["properties"] = props
            out["required"] = list(props.keys())
            out["additionalProperties"] = False
        if "allOf" in out and len(out["allOf"]) == 1:
            inner = out.pop("allOf")[0]
            out.update(inner)
        return out

    return resolve(raw)
