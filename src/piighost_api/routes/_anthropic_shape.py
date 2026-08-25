"""Field-level anonymize and deanonymize for Anthropic Messages bodies.

Only known text-bearing fields are rewritten: the system prompt, message
content blocks, tool_use inputs, and tool_result contents. Images, documents,
and tool definitions are forwarded untouched, so the proxy stays robust to the
Anthropic schema evolving. All rewriting goes through the pipeline's public
anonymize and deanonymize, over a single thread per request.
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any

from piighost.components.placeholder import AsyncPlaceholderStreamDecoder
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


DEFAULT_PLACEHOLDER_NOTE = (
    "Privacy note: this conversation has been de-identified. Some values (names, "
    "emails, phone numbers, and other personal data) are replaced by stable "
    "placeholder tokens of the form <<LABEL:N>>, such as <<PERSON:1>> or "
    "<<EMAIL:2>>. Each token always refers to the same underlying value. Treat a "
    "token as the value it stands for, and reuse the exact same token verbatim "
    "wherever you would use that value, in prose, code, tool calls, or file "
    "contents. Do not invent or guess the real value, and do not point out that a "
    "value is masked unless the user explicitly asks about anonymization. The "
    "tokens are restored to their real values before the user sees your reply, so "
    "answer naturally as if the real values were present."
)
"""Guidance prepended to the system prompt so the model handles tokens fluently."""


def inject_system_note(body: dict[str, Any], note: str) -> dict[str, Any]:
    """Prepend a guidance note to the request's system prompt, in place.

    Explains the placeholder tokens to the model so it uses them naturally. The
    note becomes the first system text block, or prefixes a string system, or
    stands alone when there is no system prompt. An empty note is a no-op.
    """
    if not note:
        return body
    system = body.get("system")
    if system is None:
        body["system"] = note
    elif isinstance(system, str):
        body["system"] = note + "\n\n" + system
    elif isinstance(system, list):
        note_block = {"type": "text", "text": note}
        body["system"] = [note_block, *system]
    return body


async def anonymize_anthropic_request(
    body: dict[str, Any],
    pipeline: ThreadAnonymizationPipeline,
    thread_id: str,
    anonymize_system: bool = True,
) -> dict[str, Any]:
    """Anonymize a Messages request body's system prompt and message content.

    When anonymize_system is False the system prompt is left untouched, which a
    subscription-authenticated harness such as Claude Code needs so the upstream
    can still validate its client fingerprint; message content is anonymized
    either way.
    """
    op = _anonymizer(pipeline, thread_id)
    if anonymize_system and "system" in body:
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


class AnthropicStreamRestorer:
    """Restore tokens in an Anthropic SSE stream, per content-block index.

    Anthropic streams typed events; only content_block_delta carries model text.
    Each block index gets its own AsyncPlaceholderStreamDecoder so a token split
    across deltas is reassembled without one block bleeding a held fragment into
    another. text_delta.text and input_json_delta.partial_json are restored the
    same way: we rewrite the decoded string value and re-serialize the event, so
    JSON escaping of the restored value is automatic. Every other line, including
    the event: lines and blank separators, passes through unchanged.
    """

    def __init__(self, replace: _StringOp) -> None:
        self._replace = replace
        self._decoders: dict[int, AsyncPlaceholderStreamDecoder] = {}

    def _decoder(self, index: int) -> AsyncPlaceholderStreamDecoder:
        """Return the per-index decoder, creating it on first use."""
        decoder = self._decoders.get(index)
        if decoder is None:
            decoder = AsyncPlaceholderStreamDecoder(self._replace)
            self._decoders[index] = decoder
        return decoder

    async def feed_line(self, raw: str) -> str:
        """Restore tokens in a data line's delta; return other lines unchanged."""
        if not raw.startswith("data:"):
            return raw
        payload = raw[len("data:") :].strip()
        if not payload:
            return raw
        try:
            event = json.loads(payload)
        except ValueError:
            return raw
        if event.get("type") != "content_block_delta":
            return raw
        index = event.get("index", 0)
        delta = event.get("delta")
        if isinstance(delta, dict):
            decoder = self._decoder(index)
            if isinstance(delta.get("text"), str):
                delta["text"] = await decoder.feed(delta["text"])
            elif isinstance(delta.get("partial_json"), str):
                delta["partial_json"] = await decoder.feed(delta["partial_json"])
        return "data: " + json.dumps(event)

    def flush(self) -> str:
        """Emit any trailing fragments the decoders still hold (truncated stream)."""
        return "".join(decoder.flush() for decoder in self._decoders.values())
