"""Unit tests for the Anthropic content-block walker and stream restorer."""

import json

import pytest

from piighost.components.detector import ExactMatchDetector
from piighost.pipeline import ThreadAnonymizationPipeline

from piighost_api.routes._anthropic_shape import (
    AnthropicStreamRestorer,
    anonymize_anthropic_request,
    deanonymize_anthropic_response,
)


@pytest.fixture
def pipeline() -> ThreadAnonymizationPipeline:
    return ThreadAnonymizationPipeline(ExactMatchDetector({"Patrick": "PERSON"}))


async def test_anonymize_string_system_and_message(pipeline) -> None:
    """A string system prompt and string message content are both anonymized."""
    body = {
        "system": "You help Patrick.",
        "messages": [{"role": "user", "content": "I am Patrick"}],
    }
    await anonymize_anthropic_request(body, pipeline, "t1")
    assert "Patrick" not in body["system"]
    assert "<<PERSON:1>>" in body["system"]
    assert "Patrick" not in body["messages"][0]["content"]
    assert "<<PERSON:1>>" in body["messages"][0]["content"]


async def test_anonymize_block_system_and_text_block(pipeline) -> None:
    """A block-list system prompt and a text content block are both anonymized."""
    body = {
        "system": [{"type": "text", "text": "Help Patrick."}],
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "I am Patrick"}]}
        ],
    }
    await anonymize_anthropic_request(body, pipeline, "t1")
    assert "Patrick" not in body["system"][0]["text"]
    assert "Patrick" not in body["messages"][0]["content"][0]["text"]
    assert "<<PERSON:1>>" in body["messages"][0]["content"][0]["text"]


async def test_anonymize_tool_use_input_and_tool_result(pipeline) -> None:
    """tool_use.input strings and tool_result.content strings are anonymized."""
    body = {
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu_1",
                        "name": "run",
                        "input": {"cmd": "echo Patrick"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_1",
                        "content": "Patrick was here",
                    }
                ],
            },
        ]
    }
    await anonymize_anthropic_request(body, pipeline, "t1")
    tool_use = body["messages"][0]["content"][0]
    tool_result = body["messages"][1]["content"][0]
    assert tool_use["input"]["cmd"] == "echo <<PERSON:1>>"
    assert tool_result["content"] == "<<PERSON:1>> was here"


async def test_image_block_is_passthrough(pipeline) -> None:
    """An image content block is forwarded unchanged."""
    image = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"},
    }
    body = {"messages": [{"role": "user", "content": [image]}]}
    await anonymize_anthropic_request(body, pipeline, "t1")
    assert body["messages"][0]["content"][0] == image


async def test_tools_definitions_are_passthrough(pipeline) -> None:
    """The top-level tools array is forwarded unchanged."""
    tools = [{"name": "run", "description": "Patrick's tool", "input_schema": {}}]
    body = {"tools": tools, "messages": [{"role": "user", "content": "hi"}]}
    await anonymize_anthropic_request(body, pipeline, "t1")
    assert body["tools"] == tools


async def _prime(pipeline: ThreadAnonymizationPipeline, thread_id: str) -> None:
    """Anonymize 'Patrick' so <<PERSON:1>> maps back to it in the thread."""
    await pipeline.anonymize("Patrick", thread_id)


async def test_deanonymize_text_block(pipeline) -> None:
    """A text block containing a placeholder is restored to its original value."""
    await _prime(pipeline, "t1")
    body = {"content": [{"type": "text", "text": "Hi <<PERSON:1>>"}]}
    await deanonymize_anthropic_response(body, pipeline, "t1")
    assert body["content"][0]["text"] == "Hi Patrick"


async def test_deanonymize_tool_use_input(pipeline) -> None:
    """A placeholder inside tool_use.input is restored to its original value."""
    await _prime(pipeline, "t1")
    body = {
        "content": [
            {
                "type": "tool_use",
                "id": "tu_1",
                "name": "run",
                "input": {"cmd": "echo <<PERSON:1>>"},
            }
        ]
    }
    await deanonymize_anthropic_response(body, pipeline, "t1")
    assert body["content"][0]["input"]["cmd"] == "echo Patrick"


def _restorer(
    pipeline: ThreadAnonymizationPipeline, thread_id: str
) -> AnthropicStreamRestorer:
    """Build a restorer that deanonymizes tokens via the given pipeline thread."""

    async def replace(token: str) -> str:
        """Deanonymize a single token via the pipeline."""
        return await pipeline.deanonymize(token, thread_id)

    return AnthropicStreamRestorer(replace)


async def test_restorer_passes_event_lines_through(pipeline) -> None:
    """Non-data lines and blank separators pass through the restorer unchanged."""
    restorer = _restorer(pipeline, "t1")
    assert await restorer.feed_line("event: content_block_delta") == (
        "event: content_block_delta"
    )
    assert await restorer.feed_line("") == ""


async def test_restorer_restores_text_split_across_deltas(pipeline) -> None:
    """A token split across two text_delta events is reassembled and restored."""
    await _prime(pipeline, "t1")
    restorer = _restorer(pipeline, "t1")
    first = (
        'data: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"text_delta","text":"Hi <<PER"}}'
    )
    second = (
        'data: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"text_delta","text":"SON:1>>"}}'
    )
    out_first = await restorer.feed_line(first)
    out_second = await restorer.feed_line(second)
    text_first = json.loads(out_first[len("data: ") :])["delta"]["text"]
    text_second = json.loads(out_second[len("data: ") :])["delta"]["text"]
    combined = text_first + text_second
    assert combined == "Hi Patrick"
    assert "<<PERSON" not in combined


async def test_restorer_restores_tool_input_json_safely(pipeline) -> None:
    """A placeholder inside input_json_delta.partial_json is restored and re-serialized."""
    await _prime(pipeline, "t1")
    restorer = _restorer(pipeline, "t1")
    line = (
        'data: {"type":"content_block_delta","index":1,'
        '"delta":{"type":"input_json_delta","partial_json":"{\\"cmd\\": \\"echo <<PERSON:1>>\\"}"}}'
    )
    out = await restorer.feed_line(line)
    event = json.loads(out[len("data: ") :])
    assert event["delta"]["partial_json"] == '{"cmd": "echo Patrick"}'


async def test_restorer_per_index_decoders_do_not_bleed(pipeline) -> None:
    """A held fragment in one block index does not bleed into a different block index."""
    await _prime(pipeline, "t1")
    restorer = _restorer(pipeline, "t1")
    # Index 0 holds an open fragment; index 1 must not consume it.
    open_frag = (
        'data: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"text_delta","text":"<<PER"}}'
    )
    other = (
        'data: {"type":"content_block_delta","index":1,'
        '"delta":{"type":"text_delta","text":"clean"}}'
    )
    await restorer.feed_line(open_frag)
    out_other = await restorer.feed_line(other)
    text_other = json.loads(out_other[len("data: ") :])["delta"]["text"]
    assert text_other == "clean"


async def test_restorer_flush_emits_trailing_fragment(pipeline) -> None:
    """flush() returns the incomplete token fragment still held after the last delta."""
    await _prime(pipeline, "t1")
    restorer = _restorer(pipeline, "t1")
    line = (
        'data: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"text_delta","text":"end <<PER"}}'
    )
    await restorer.feed_line(line)
    assert restorer.flush() == "<<PER"
