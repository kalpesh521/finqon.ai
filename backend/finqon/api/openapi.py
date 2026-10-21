"""OpenAPI customisation."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from finqon.schemas.chat import chat_event_adapter


def install_chat_event_schema(app: FastAPI) -> None:
    """Expose the ``ChatEvent`` union in OpenAPI so clients can generate types for the stream."""
    original = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = original()
        event_schema = chat_event_adapter.json_schema(ref_template="#/components/schemas/{model}")
        definitions = event_schema.pop("$defs", {})
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        components.update(definitions)
        components["ChatEvent"] = event_schema
        # The chat route only produces an event stream; drop FastAPI's empty JSON entry.
        ok_content = schema["paths"]["/api/v1/chat"]["post"]["responses"]["200"]["content"]
        ok_content.pop("application/json", None)
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
