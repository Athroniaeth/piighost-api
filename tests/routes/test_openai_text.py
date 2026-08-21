"""Tests for the OpenAI-proxy legacy completions, embeddings, moderations."""

import httpx
import pytest
import respx
from litestar import Litestar
from litestar.testing import TestClient

from piighost.components.detector import ExactMatchDetector
from piighost.pipeline import ThreadAnonymizationPipeline

from piighost_api.routes.openai import build_openai_router


@pytest.fixture
def client() -> TestClient:
    detector = ExactMatchDetector({"Patrick": "PERSON"})
    pipeline = ThreadAnonymizationPipeline(detector)
    app = Litestar(route_handlers=[build_openai_router(pipeline)])
    return TestClient(app=app)


def _headers() -> dict[str, str]:
    return {
        "x-piighost-upstream": "https://up.example/v1",
        "authorization": "Bearer x",
        "content-type": "application/json",
    }


@respx.mock
def test_embeddings_anonymizes_input(client: TestClient) -> None:
    """The embeddings input reaches the upstream as tokens; the vector is relayed."""
    route = respx.post("https://up.example/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})
    )
    response = client.post(
        "/openai/v1/embeddings",
        headers=_headers(),
        json={"model": "text-embedding-3-small", "input": "Patrick"},
    )
    assert response.status_code == 200
    assert "Patrick" not in route.calls.last.request.content.decode()
    assert response.json() == {"data": [{"embedding": [0.1, 0.2]}]}


@respx.mock
def test_completions_anonymizes_prompt_and_restores_text(client: TestClient) -> None:
    """Legacy completions anonymize the prompt and restore choices[].text."""
    respx.post("https://up.example/v1/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"text": "<<PERSON:1>> ok"}]}
        )
    )
    response = client.post(
        "/openai/v1/completions",
        headers=_headers(),
        json={"model": "gpt-3.5-turbo-instruct", "prompt": "Patrick"},
    )
    assert response.json()["choices"][0]["text"] == "Patrick ok"


@respx.mock
def test_completions_anonymizes_the_suffix_field(client: TestClient) -> None:
    """The fill-in-the-middle `suffix` field is anonymized too, not just the prompt."""
    route = respx.post("https://up.example/v1/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"text": "ok"}]})
    )
    client.post(
        "/openai/v1/completions",
        headers=_headers(),
        json={
            "model": "gpt-3.5-turbo-instruct",
            "prompt": "hi",
            "suffix": "from Patrick",
        },
    )
    assert "Patrick" not in route.calls.last.request.content.decode()


def test_non_object_body_is_400(client: TestClient) -> None:
    """A valid-JSON but non-object body is a 400, not a 500."""
    response = client.post("/openai/v1/embeddings", headers=_headers(), json=[1, 2, 3])
    assert response.status_code == 400
