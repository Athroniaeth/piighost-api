# OpenAI-compatible proxy: design

**Date:** 2026-08-21
**Repo:** piighost-api (feature branch `feat/openai-proxy`)
**Related lib roadmap item:** "OpenAI-compatible proxy" in the piighost library docs

## Goal

Move PII de-identification to the HTTP boundary. piighost-api exposes an
OpenAI-compatible API under `/openai/v1`, so an application changes only its
`base_url` (and adds one header) and needs no other code. The proxy anonymizes
the request, forwards it to a caller-chosen upstream OpenAI-compatible API,
deanonymizes the reply, and returns it in the OpenAI shape, so the upstream
provider only ever sees tokens like `<<PERSON:1>>`, never `Patrick`.

## Scope (v1)

A `Router(path="/openai/v1")` mounted in `create_app`. Chat Completions is the
stateful-of-tokens route; the rest are lighter. The Assistants API
(`/v1/threads`) is explicitly NOT proxied: it is server-side stateful, almost no
OpenAI-compatible provider implements it, and OpenAI sunsets it on 2026-08-26.
The Responses API (`/v1/responses`) is a possible v2, not v1.

| Route | Handling |
|-------|----------|
| `POST /openai/v1/chat/completions` | anonymize messages + tool_call arguments, deanonymize the reply, streaming supported |
| `POST /openai/v1/completions` (legacy) | anonymize `prompt`, deanonymize `choices[].text` |
| `POST /openai/v1/embeddings` | anonymize `input`, relay the vector untouched (nothing to restore) |
| `POST /openai/v1/moderations` | anonymize `input`, relay the scores |
| `GET /openai/v1/models`, `/openai/v1/models/{id}` | pure relay, no PII |
| `POST /openai/v1/images/{generations,edits,variations}` | pure relay (multipart/binary), no anonymization |
| `POST /openai/v1/audio/{speech,transcriptions,translations}` | pure relay (multipart/binary), no anonymization |

An unknown path under `/openai/v1/*` returns 404 (fail closed): the proxy never
blindly relays an unhandled route, so a future text route cannot leak PII in the
clear. Multimodal routes are relayed on purpose, since multimodal
de-identification is a documented non-goal; they carry no text the proxy would
tokenize.

## Upstream and auth (transparent relay)

- Header **`X-PIIGhost-Upstream`** carries the upstream base URL
  (e.g. `https://api.openai.com/v1`, an Azure endpoint, a self-hosted vLLM,
  OpenRouter). Missing on a route that needs it -> 400.
- The caller's **`Authorization: Bearer`** header is relayed as-is to that
  upstream (the OpenAI SDK's `api_key` becomes the upstream key). The proxy
  imposes no keyshield auth of its own on `/openai` (`exclude_from_auth=True`).
- The proxy consumes the `X-PIIGhost-*` headers, forwards `Authorization` and
  `Content-Type`, and drops hop-by-hop headers.

## Threading

- Header **`X-PIIGhost-Thread-Id`** is optional.
  - Absent -> an ephemeral thread: a fresh unique id per request; all the
    request's text is anonymized into it, the reply is deanonymized from it,
    then `pipeline.forget_thread(thread_id)` purges it. The memory backend keeps
    nothing, so there is no growth even under Redis.
  - Present -> that fixed thread id is used and NOT forgotten. The caller owns
    its lifetime and can purge it via the existing `DELETE /v1/threads/{id}`.
    A fixed thread gives cross-request token stability and detection caching on
    resent history.
- The application never sees tokens (they are restored before the reply
  returns), so cross-request token stability is irrelevant to correctness; it is
  only an opt-in for callers who want it.

## Request and response rewriting

The body is not modeled as a strict schema, because the OpenAI API evolves.
Decode the JSON to a dict, rewrite only the known text fields, and forward the
rest untouched. All rewriting uses the pipeline's PUBLIC API
(`ThreadAnonymizationPipeline.anonymize` / `.deanonymize`), never a private lib
module.

- chat/completions request -> anonymize `messages[*].content` (all roles:
  system, user, assistant, tool) and `messages[*].tool_calls[*].function.arguments`
  (a JSON string: `json.loads`, anonymize each contained string, then
  `json.dumps`).
- chat/completions reply -> deanonymize `choices[*].message.content` and
  `choices[*].message.tool_calls[*].function.arguments`.
- completions request -> anonymize `prompt` (string or list of strings); reply
  -> deanonymize `choices[*].text`.
- embeddings / moderations request -> anonymize `input` (string or list); reply
  relayed untouched.

Same value maps to the same token within the request's thread, so a repeated
name is one consistent token to the model.

## Streaming (`stream: true`)

Relay the upstream SSE with an httpx streaming request. For each `data:` chunk,
feed the text deltas (`choices[*].delta.content` and
`choices[*].delta.tool_calls[*].function.arguments`) through a per-channel
`AsyncPlaceholderStreamDecoder` (from `piighost.components.placeholder`), with
`pipeline.deanonymize` as the `replace` callback, so a token split across chunks
is reassembled and restored. Flush each decoder's buffer at stream end. The
decoder restores `<<TOKEN>>` wherever it appears, so it covers content deltas and
tool-call-argument deltas uniformly. An ephemeral thread is forgotten after the
stream closes.

## Pure relay (models, images, audio)

A single generic passthrough helper serves every non-anonymizing route. It
forwards the method, `Authorization`, `Content-Type`, and the raw request body
bytes to `<upstream>/<subpath>`, and returns the upstream status, content type,
and raw response bytes. Working at the byte level means it handles JSON,
multipart file uploads (`images/edits`, `audio/transcriptions`), and binary
responses (audio, images) without decoding anything. No anonymization runs on
these routes.

## Where it lives and dependencies

Entirely in piighost-api, over the piighost library's PUBLIC API. The API
already holds a `ThreadAnonymizationPipeline` from `load_thread_pipeline`; the
proxy reuses it, plus `AsyncPlaceholderStreamDecoder` for streaming. No library
change and no new piighost release are needed. Confirm the pinned lib version
exposes both pieces and bump the pin to `piighost[config,redis]>=1.3` if needed
for clarity.

New dependency: `httpx` (outbound client, streaming and byte passthrough). New
module `src/piighost_api/routes/openai.py` exports the handler list; `create_app`
mounts it via `Router(path="/openai/v1", route_handlers=[...])`.

## Error handling

- Missing `X-PIIGhost-Upstream` on a route that needs it -> 400.
- Upstream unreachable or timing out -> 502.
- Upstream returns an error status (401, 429, 4xx, 5xx) -> relayed as-is (status
  and body; the body is deanonymized on the anonymizing routes).
- Body is not JSON, or not a recognizable chat/completions/embeddings body, on an
  anonymizing route -> 400 with a clear message.
- Unknown path under `/openai/v1` -> 404.

## Testing

Litestar `TestClient` with the pipeline mocked (as the existing suite does) and
the outbound httpx upstream mocked, so no real network call runs. Cases:

- the upstream receives only tokens, never the original PII (assert on the
  captured forwarded body);
- the returned reply is deanonymized (real values restored);
- ephemeral thread is forgotten after the reply; a supplied `X-PIIGhost-Thread-Id`
  is used and not forgotten;
- tool_call arguments are anonymized outbound and deanonymized inbound;
- a streaming response: tokenized SSE chunks in -> restored chunks out, including
  a token split across two chunks;
- pure relay: a multipart or binary passthrough forwards bytes unchanged and
  returns the upstream bytes, with no anonymization;
- errors: missing upstream header -> 400, upstream 4xx relayed, unknown route ->
  404.

## Documentation

- piighost-api docs (`docs/en` + `docs/fr`, byte-identical code blocks) and the
  README(s): a page documenting the `/openai/v1` proxy, the two headers
  (`X-PIIGhost-Upstream`, `X-PIIGhost-Thread-Id`), the route map, the transparent
  auth model, and the ephemeral-vs-fixed thread behavior.
- piighost LIBRARY docs (the roadmap "OpenAI-compatible proxy" section): update
  it to state that the proxy is built in piighost-api, and to spell out the
  boundary, what the library provides (the conversation pipeline, the
  `AsyncPlaceholderStreamDecoder`, the tool-boundary de-identification) versus
  what the library does NOT do (it is not itself an HTTP proxy; the proxy is an
  piighost-api concern). This is a cross-repo doc task.

## Out of scope

- The Assistants API (`/v1/threads`) and the Responses API (`/v1/responses`):
  stateful and, for Assistants, deprecated. A Responses-API proxy could be a v2.
- Multimodal de-identification: images and audio are relayed untouched, matching
  the library's multimodal non-goal.
- A keyshield auth guard on `/openai`: the transparent relay forwards the
  caller's own upstream key, so the proxy adds no access control of its own. A
  future deployment that wants to gate proxy access can add it.
- A cross-request result cache surviving ephemeral threads: that is the library's
  separate "optional result cache" roadmap item, not this proxy.
