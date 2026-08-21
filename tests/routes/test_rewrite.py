"""Tests for the OpenAI-proxy body rewriting, over an offline pipeline."""

from piighost.components.detector import ExactMatchDetector
from piighost.pipeline import ThreadAnonymizationPipeline

from piighost_api.routes._rewrite import (
    anonymize_chat_request,
    deanonymize_chat_response,
)


def _pipeline() -> ThreadAnonymizationPipeline:
    detector = ExactMatchDetector({"Patrick": "PERSON", "Paris": "LOCATION"})
    return ThreadAnonymizationPipeline(detector)


async def test_anonymize_chat_request_rewrites_message_content() -> None:
    """Every message's string content is anonymized into the thread."""
    pipeline = _pipeline()
    body = {"messages": [{"role": "user", "content": "Patrick lives in Paris"}]}
    result = await anonymize_chat_request(body, pipeline, "t")
    assert result["messages"][0]["content"] == "<<PERSON:1>> lives in <<LOCATION:1>>"


async def test_anonymize_chat_request_rewrites_tool_call_arguments() -> None:
    """A tool_call's JSON arguments have their string values anonymized."""
    pipeline = _pipeline()
    body = {
        "messages": [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "send",
                            "arguments": '{"to": "Patrick"}',
                        },
                    }
                ],
            }
        ]
    }
    result = await anonymize_chat_request(body, pipeline, "t")
    args = result["messages"][0]["tool_calls"][0]["function"]["arguments"]
    assert args == '{"to": "<<PERSON:1>>"}'


async def test_deanonymize_chat_response_restores_content_and_tool_args() -> None:
    """The reply's content and tool_call arguments are restored."""
    pipeline = _pipeline()
    # Prime the thread so the tokens are known.
    await anonymize_chat_request(
        {"messages": [{"role": "user", "content": "Patrick in Paris"}]},
        pipeline,
        "t",
    )
    response = {
        "choices": [
            {
                "message": {
                    "content": "<<PERSON:1>> is in <<LOCATION:1>>",
                    "tool_calls": [
                        {"function": {"arguments": '{"who": "<<PERSON:1>>"}'}}
                    ],
                }
            }
        ]
    }
    result = await deanonymize_chat_response(response, pipeline, "t")
    message = result["choices"][0]["message"]
    assert message["content"] == "Patrick is in Paris"
    assert message["tool_calls"][0]["function"]["arguments"] == '{"who": "Patrick"}'
