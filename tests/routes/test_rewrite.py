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


async def test_system_and_developer_messages_stay_in_clear_by_default() -> None:
    """The developer's own prompt is relayed as written, the user turn is not."""
    pipeline = _pipeline()
    body = {
        "messages": [
            {"role": "system", "content": "You help Patrick."},
            {"role": "developer", "content": [{"type": "text", "text": "Paris"}]},
            {"role": "user", "content": "Patrick lives in Paris"},
        ]
    }
    result = await anonymize_chat_request(body, pipeline, "t")
    messages = result["messages"]
    assert messages[0]["content"] == "You help Patrick."
    assert messages[1]["content"] == [{"type": "text", "text": "Paris"}]
    assert messages[2]["content"] == "<<PERSON:1>> lives in <<LOCATION:1>>"


async def test_system_and_developer_messages_anonymized_when_opted_in() -> None:
    """With anonymize_system, the system and developer messages are rewritten too."""
    pipeline = _pipeline()
    body = {
        "messages": [
            {"role": "system", "content": "You help Patrick."},
            {"role": "developer", "content": "Answer from Paris."},
        ]
    }
    result = await anonymize_chat_request(body, pipeline, "t", anonymize_system=True)
    assert result["messages"][0]["content"] == "You help <<PERSON:1>>."
    assert result["messages"][1]["content"] == "Answer from <<LOCATION:1>>."
