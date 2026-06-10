"""Anthropic API helpers shared by all agent pipelines.

Three call shapes cover the whole platform:
- stream_text():   respondent-facing chat (interviews) — SSE-friendly generator
- complete():      one-shot text generation
- complete_json(): structured outputs validated against a JSON schema
- research():      web-search-grounded generation for literature/desk research
"""
import json
import anthropic

from .config import anthropic_api_key, DEFAULT_PIPELINE_MODEL


def get_client():
    key = anthropic_api_key()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    return anthropic.Anthropic(api_key=key)


def stream_text(system, messages, model, max_tokens=2048):
    """Yield text chunks for SSE streaming to the browser."""
    client = get_client()
    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
    ) as stream:
        for text in stream.text_stream:
            yield text


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
    return "".join(b.text for b in message.content if b.type == "text")


def _strict_schema(node):
    """The API requires additionalProperties: false on every object node."""
    if isinstance(node, dict):
        if node.get("type") == "object":
            node.setdefault("additionalProperties", False)
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
