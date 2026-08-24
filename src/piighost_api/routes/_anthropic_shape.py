"""Field-level anonymize and deanonymize for Anthropic Messages bodies.

Only known text-bearing fields are rewritten: the system prompt, message
content blocks, tool_use inputs, and tool_result contents. Images, documents,
and tool definitions are forwarded untouched, so the proxy stays robust to the
Anthropic schema evolving. All rewriting goes through the pipeline's public
anonymize and deanonymize, over a single thread per request.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from piighost.pipeline import ThreadAnonymizationPipeline

from piighost_api.routes._rewrite import _anonymizer, _deanonymizer, _map_strings

_StringOp = Callable[[str], Awaitable[str]]
"""A coroutine that maps one string to another, used for all field rewrites."""


async def _rewrite_block(block: Any, op: _StringOp) -> Any:
    """Rewrite one content block by type; pass unknown or binary blocks through."""
    if not isinstance(block, dict):
        return block
    block_type = block.get("type")
    if block_type == "text" and isinstance(block.get("text"), str):
        return {**block, "text": await op(block["text"])}
    if block_type == "tool_use" and isinstance(block.get("input"), (dict, list)):
        return {**block, "input": await _map_strings(block["input"], op)}
    if block_type == "tool_result" and "content" in block:
        return {**block, "content": await _rewrite_content(block["content"], op)}
    return block


async def _rewrite_content(content: Any, op: _StringOp) -> Any:
    """Rewrite a message content: a string, or a list of content blocks."""
    if isinstance(content, str):
        return await op(content)
    if isinstance(content, list):
        return [await _rewrite_block(block, op) for block in content]
    return content


async def _rewrite_system(system: Any, op: _StringOp) -> Any:
    """Rewrite the system prompt: a string, or a list of text blocks."""
    if isinstance(system, str):
        return await op(system)
    if isinstance(system, list):
        rewritten = []
        for block in system:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                block = {**block, "text": await op(block["text"])}
            rewritten.append(block)
        return rewritten
    return system


async def _rewrite_messages(messages: Any, op: _StringOp) -> None:
    """Rewrite each message's content in place."""
    if not isinstance(messages, list):
        return
    for message in messages:
        if isinstance(message, dict) and "content" in message:
            message["content"] = await _rewrite_content(message["content"], op)


async def anonymize_anthropic_request(
    body: dict[str, Any], pipeline: ThreadAnonymizationPipeline, thread_id: str
) -> dict[str, Any]:
    """Anonymize a Messages request body's system prompt and message content."""
    op = _anonymizer(pipeline, thread_id)
    if "system" in body:
        body["system"] = await _rewrite_system(body["system"], op)
    await _rewrite_messages(body.get("messages"), op)
    return body


async def deanonymize_anthropic_response(
    body: dict[str, Any], pipeline: ThreadAnonymizationPipeline, thread_id: str
) -> dict[str, Any]:
    """Deanonymize a Messages response body's content blocks."""
    op = _deanonymizer(pipeline, thread_id)
    if isinstance(body.get("content"), list):
        body["content"] = [await _rewrite_block(block, op) for block in body["content"]]
    return body
