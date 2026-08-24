"""Unit tests for the Anthropic content-block walker and stream restorer."""

import pytest

from piighost.components.detector import ExactMatchDetector
from piighost.pipeline import ThreadAnonymizationPipeline

from piighost_api.routes._anthropic_shape import (
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
