---
icon: lucide/link
---

# OpenAI-compatible proxy

`piighost-api` can act as a transparent OpenAI-compatible proxy under `/openai/v1`. Point your OpenAI client's `base_url` at the proxy, name the real upstream in a header, and the proxy anonymizes every request before it leaves, forwards it to that upstream, and deanonymizes the reply. The upstream provider only ever sees tokens like `<<PERSON:1>>`, never `Patrick`.

## Two headers

- `X-PIIGhost-Upstream` (required): the base URL of the OpenAI-compatible endpoint to forward to, for example `https://api.openai.com/v1`, an Azure endpoint, a self-hosted vLLM, or OpenRouter.
- `X-PIIGhost-Thread-Id` (optional): pin a fixed anonymization thread across requests. Absent, each request uses an ephemeral thread that is forgotten as soon as the reply is returned.

The caller's `Authorization: Bearer` header is forwarded as-is to the upstream, so your OpenAI client's API key becomes the upstream key. The proxy adds no auth of its own on `/openai`.

## Routes

| Route | What the proxy does |
|-------|---------------------|
| `POST /openai/v1/chat/completions` | anonymizes messages and tool-call arguments, deanonymizes the reply; streaming supported |
| `POST /openai/v1/completions` | anonymizes the prompt, deanonymizes the completion text |
| `POST /openai/v1/embeddings` | anonymizes the input, relays the vector |
| `POST /openai/v1/moderations` | anonymizes the input, relays the scores |
| `GET /openai/v1/models`, `/openai/v1/models/{id}` | pure relay, no PII |
| `POST /openai/v1/images/*`, `/openai/v1/audio/*` | pure relay (multipart and binary), no anonymization |

An unknown route under `/openai/v1` returns 404: the proxy never blindly relays an unhandled route, so no future text route can leak PII.

## Point your client at the proxy

With the OpenAI Python SDK, change only the `base_url` and add the upstream header:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/openai/v1",
    api_key="sk-your-upstream-key",
    default_headers={"X-PIIGhost-Upstream": "https://api.openai.com/v1"},
)
response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Email Patrick at patrick@example.com"}],
)
```

The upstream received `<<PERSON:1>>` and `<<EMAIL:1>>`; the response carries `Patrick` and `patrick@example.com` restored.

Or with curl:

```bash
curl http://localhost:8000/openai/v1/chat/completions \
  -H "Authorization: Bearer sk-your-upstream-key" \
  -H "X-PIIGhost-Upstream: https://api.openai.com/v1" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "I am Patrick"}]}'
```

## What the library does, and what it does not

The anonymization comes from the `piighost` library: the conversation pipeline, the streaming token decoder, and the tool-boundary de-identification. `piighost-api` wires those pieces behind HTTP. The library is not itself an HTTP proxy; the proxy is a `piighost-api` concern.

## Known limitations

- Streaming errors surface as a broken stream, not a status. A streaming request commits to HTTP 200 before the upstream stream opens, so an upstream error mid-stream reaches the client as a truncated stream rather than a clean error status. A non-streaming request relays the upstream status faithfully.
- Images and audio are relayed untouched. Multimodal de-identification is out of scope, and those routes carry no text the proxy tokenizes.
- The Assistants API (`/v1/threads`) is not proxied. It is server-side stateful and being retired by OpenAI, whereas Chat Completions is stateless and universal.
