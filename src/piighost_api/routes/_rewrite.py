"""Field-level anonymize and deanonymize for OpenAI request and response bodies.

Only known text fields are rewritten; everything else is forwarded untouched, so
the proxy stays robust to the OpenAI schema evolving. All rewriting goes through
the pipeline's public anonymize and deanonymize, over a single thread per request.
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any

from piighost.pipeline import ThreadAnonymizationPipeline

_StringOp = Callable[[str], Awaitable[str]]


async def _map_strings(value: Any, op: _StringOp) -> Any:
    """Apply op to every string inside nested dicts and lists, in place of value."""
    if isinstance(value, str):
        return await op(value)
    if isinstance(value, dict):
        return {key: await _map_strings(item, op) for key, item in value.items()}
    if isinstance(value, list):
        return [await _map_strings(item, op) for item in value]
    return value


async def _rewrite_json_string(raw: str, op: _StringOp) -> str:
    """Rewrite the string values inside a JSON string, or the string itself.

    A tool_call's arguments are a JSON string. When it parses, rewrite every
    string it holds and re-serialize; when it does not, treat it as plain text.
    """
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return await op(raw)
    rewritten = await _map_strings(parsed, op)
    return json.dumps(rewritten)


async def _rewrite_content(content: Any, op: _StringOp) -> Any:
    """Rewrite a message content, a string or a list of typed content parts."""
    if isinstance(content, str):
        return await op(content)
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                part = {**part, "text": await op(part["text"])}
            parts.append(part)
        return parts
    return content


async def _rewrite_messages(messages: Any, op: _StringOp) -> None:
    """Rewrite message content and tool_call arguments in place."""
    if not isinstance(messages, list):
        return
    for message in messages:
        if not isinstance(message, dict):
            continue
        if "content" in message:
            message["content"] = await _rewrite_content(message["content"], op)
        for tool_call in message.get("tool_calls") or []:
            function = (
                tool_call.get("function") if isinstance(tool_call, dict) else None
            )
            if isinstance(function, dict) and isinstance(
                function.get("arguments"), str
            ):
                function["arguments"] = await _rewrite_json_string(
                    function["arguments"], op
                )


def _anonymizer(pipeline: ThreadAnonymizationPipeline, thread_id: str) -> _StringOp:
    """A string op that anonymizes into the thread."""

    async def op(text: str) -> str:
        result = await pipeline.anonymize(text, thread_id)
        return result.text

    return op


def _deanonymizer(pipeline: ThreadAnonymizationPipeline, thread_id: str) -> _StringOp:
    """A string op that deanonymizes from the thread."""

    async def op(text: str) -> str:
        return await pipeline.deanonymize(text, thread_id)

    return op


async def anonymize_chat_request(
    body: dict[str, Any], pipeline: ThreadAnonymizationPipeline, thread_id: str
) -> dict[str, Any]:
    """Anonymize a chat/completions request body's messages and tool_call args."""
    await _rewrite_messages(body.get("messages"), _anonymizer(pipeline, thread_id))
    return body


async def deanonymize_chat_response(
    body: dict[str, Any], pipeline: ThreadAnonymizationPipeline, thread_id: str
) -> dict[str, Any]:
    """Deanonymize a chat/completions response body's choices."""
    op = _deanonymizer(pipeline, thread_id)
    for choice in body.get("choices") or []:
        message = choice.get("message") if isinstance(choice, dict) else None
        if not isinstance(message, dict):
            continue
        if "content" in message:
            message["content"] = await _rewrite_content(message["content"], op)
        for tool_call in message.get("tool_calls") or []:
            function = (
                tool_call.get("function") if isinstance(tool_call, dict) else None
            )
            if isinstance(function, dict) and isinstance(
                function.get("arguments"), str
            ):
                function["arguments"] = await _rewrite_json_string(
                    function["arguments"], op
                )
    return body


async def anonymize_input_field(
    body: dict[str, Any], pipeline: ThreadAnonymizationPipeline, thread_id: str
) -> dict[str, Any]:
    """Anonymize the `input` field (string or list) for embeddings and moderations."""
    if "input" in body:
        body["input"] = await _map_strings(
            body["input"], _anonymizer(pipeline, thread_id)
        )
    return body


async def anonymize_prompt_field(
    body: dict[str, Any], pipeline: ThreadAnonymizationPipeline, thread_id: str
) -> dict[str, Any]:
    """Anonymize the `prompt` and `suffix` fields for legacy completions."""
    op = _anonymizer(pipeline, thread_id)
    for field in ("prompt", "suffix"):
        if field in body:
            body[field] = await _map_strings(body[field], op)
    return body


async def deanonymize_completion_response(
    body: dict[str, Any], pipeline: ThreadAnonymizationPipeline, thread_id: str
) -> dict[str, Any]:
    """Deanonymize a legacy completions response body's choices[].text."""
    op = _deanonymizer(pipeline, thread_id)
    for choice in body.get("choices") or []:
        if isinstance(choice, dict) and isinstance(choice.get("text"), str):
            choice["text"] = await op(choice["text"])
    return body
