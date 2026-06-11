"""Anthropic API helpers shared by all agent pipelines.

Three call shapes cover the whole platform:
- stream_text():   respondent-facing chat (interviews) — SSE-friendly generator
- complete():      one-shot text generation
- complete_json(): structured outputs validated against a JSON schema
- research():      web-search-grounded generation for literature/desk research
"""
import inspect
import json
import anthropic

from .config import anthropic_api_key, DEFAULT_PIPELINE_MODEL


def _caller_module():
    """The first stack frame outside this file — i.e. which platform module
    made the AI call. Used for the ai_usage_log audit trail."""
    try:
        for frame_info in inspect.stack()[2:]:
            name = frame_info.frame.f_globals.get("__name__", "")
            if name and name != __name__:
                return name
    except Exception:
        pass
    return ""


def _log_usage(model, usage=None, approx_chars=0):
    """Append one row to ai_usage_log. Never raises — auditing must not break
    the call it audits. user_id comes from the Flask session when in a request;
    job_id from the background-job context; project_id stays null unless a
    future caller supplies richer context."""
    try:
        from . import db
        from .jobs import current_job_id
        tokens = 0
        if usage is not None:
            tokens = int(getattr(usage, "input_tokens", 0) or 0) + \
                     int(getattr(usage, "output_tokens", 0) or 0)
        if not tokens:
            tokens = max(approx_chars // 4, 0)
        user_id = None
        try:
            from flask import has_request_context, session
            if has_request_context():
                user_id = (session.get("user") or {}).get("user_id")
        except Exception:
            pass
        db.insert("ai_usage_log", {
            "model": str(model or "unknown"),
            "module": _caller_module(),
            "job_id": current_job_id.get(),
            "project_id": None,
            "user_id": user_id,
            "timestamp": db.now(),
            "token_count_approx": tokens,
        })
    except Exception:
        pass


def get_client():
    key = anthropic_api_key()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    return anthropic.Anthropic(api_key=key)


def stream_text(system, messages, model, max_tokens=2048):
    """Yield text chunks for SSE streaming to the browser."""
    client = get_client()
    chars = len(str(system)) + sum(len(str(m.get("content", ""))) for m in messages)
    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
    ) as stream:
        for text in stream.text_stream:
            chars += len(text)
            yield text
    _log_usage(model, approx_chars=chars)


def complete(system, prompt, model=DEFAULT_PIPELINE_MODEL, max_tokens=16000, thinking=True):
    """One-shot generation; returns the response text."""
    client = get_client()
    kwargs = {}
    if thinking:
        kwargs["thinking"] = {"type": "adaptive"}
    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        **kwargs,
    ) as stream:
        message = stream.get_final_message()
    _log_usage(model, usage=getattr(message, "usage", None))
    return "".join(b.text for b in message.content if b.type == "text")


# JSON Schema features the structured-outputs API rejects with a 400. Counts
# and ranges belong in the prompt text instead; stripping them only loosens
# validation, never changes the requested shape.
_UNSUPPORTED_SCHEMA_KEYS = (
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "pattern", "maxItems", "uniqueItems",
)


def _strict_schema(node):
    """Normalize a schema to the API's supported JSON Schema subset:
    objects need additionalProperties: false; numeric/string/array
    constraints (minItems>1, maximum, maxLength, ...) are not supported."""
    if isinstance(node, dict):
        if node.get("type") == "object":
            node.setdefault("additionalProperties", False)
        for key in _UNSUPPORTED_SCHEMA_KEYS:
            node.pop(key, None)
        if isinstance(node.get("minItems"), int) and node["minItems"] > 1:
            node["minItems"] = 1
        for value in node.values():
            _strict_schema(value)
    elif isinstance(node, list):
        for item in node:
            _strict_schema(item)
    return node


def complete_json(system, prompt, schema, model=DEFAULT_PIPELINE_MODEL, max_tokens=16000):
    """Structured generation; returns a dict matching `schema` (JSON Schema)."""
    schema = _strict_schema(schema)
    client = get_client()
    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    ) as stream:
        message = stream.get_final_message()
    _log_usage(model, usage=getattr(message, "usage", None))
    text = next(b.text for b in message.content if b.type == "text")
    return json.loads(text)


def research(system, prompt, model=DEFAULT_PIPELINE_MODEL, max_tokens=32000, max_searches=15):
    """Web-search-grounded generation for literature and desk research.

    Returns (text, citations) where citations is a list of
    {url, title} dicts collected from the search result blocks.
    """
    client = get_client()
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches}]
    messages = [{"role": "user", "content": prompt}]
    citations = []
    text_parts = []

    # Server-side tool loops can pause (stop_reason == "pause_turn"); re-send to resume.
    for _ in range(8):
        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            tools=tools,
            thinking={"type": "adaptive"},
        ) as stream:
            response = stream.get_final_message()
        _log_usage(model, usage=getattr(response, "usage", None))

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
                for c in getattr(block, "citations", None) or []:
                    url = getattr(c, "url", None)
                    if url and url not in [x["url"] for x in citations]:
                        citations.append({"url": url, "title": getattr(c, "title", "") or ""})
            elif block.type == "web_search_tool_result":
                for item in getattr(block, "content", None) or []:
                    url = getattr(item, "url", None)
                    if url and url not in [x["url"] for x in citations]:
                        citations.append({"url": url, "title": getattr(item, "title", "") or ""})

        if response.stop_reason != "pause_turn":
            break
        messages = [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": response.content},
        ]
        text_parts = []

    return "".join(text_parts), citations
