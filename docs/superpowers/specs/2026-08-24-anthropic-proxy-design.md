# Anthropic-compatible proxy design

**Date:** 2026-08-24
**Repo:** `piighost-api`
**Status:** approved, pending implementation plan

## Goal

Add an Anthropic Messages-compatible proxy endpoint to `piighost-api` so a coding-agent
harness such as Claude Code can point `ANTHROPIC_BASE_URL` at the proxy and have its PII
de-identified transparently. The model only ever sees placeholder tokens, and the reply is
restored before it reaches the harness. This mirrors the existing OpenAI-compatible proxy
under `/openai/v1`, reusing the anonymization core unchanged.

## Approach

Approach A: a dedicated route module plus a light extraction of the genuinely shared
helpers. The anonymization core (`ThreadAnonymizationPipeline`), upstream resolution, header
forwarding, the string-deanonymization walk, and the `AsyncPlaceholderStreamDecoder` wiring
are reused. The only genuinely new code is an Anthropic-shape field walker and an Anthropic
SSE event parser. We do not generalize the OpenAI proxy into a provider-agnostic engine
(approach B) because the two SSE shapes differ enough that the abstraction would leak, and
two providers do not justify it yet.

## File structure

- `src/piighost_api/routes/anthropic.py` (create): the two routes
  `POST /anthropic/v1/messages` and `POST /anthropic/v1/messages/count_tokens`. Claude Code
  calls `ANTHROPIC_BASE_URL` + `/v1/messages`, so the operator sets
  `ANTHROPIC_BASE_URL=http://host/anthropic`, exactly as `/openai/v1` works today.
- `src/piighost_api/routes/_anthropic_shape.py` (create): the Anthropic content-block field
  walker (request anonymization and response deanonymization) and the Anthropic SSE event
  parser. This is the only substantial new logic.
- `src/piighost_api/routes/_upstream.py` (modify): `upstream_base_url()` gains a `default`
  parameter. `X-PIIGhost-Upstream` still wins when present; otherwise it falls back to the
  configured default. It raises 400 only when neither a header nor a default is available.
- `src/piighost_api/routes/_upstream.py` / `forward_headers` (modify): also relay
  `x-api-key`, `anthropic-version`, and `anthropic-beta` alongside `authorization` and
  `content-type`. PIIGhost and hop-by-hop headers stay dropped.
- App wiring (modify): register the Anthropic router; add settings for the per-route default
  upstream (`PIIGHOST_ANTHROPIC_UPSTREAM` defaulting to `https://api.anthropic.com`, and the
  symmetric OpenAI default).

## Upstream resolution and headers

Per-route default upstream configured server-side, overridable by the `X-PIIGhost-Upstream`
header when the caller can set it. Claude Code cannot easily add a custom header, so the
default makes it work out of the box; the header keeps parity with the OpenAI proxy for
callers that can set it. Making the header optional on the OpenAI proxy does not break its
tests, which always send the header.

Header forwarding relays `x-api-key`, `authorization`, `anthropic-version`,
`anthropic-beta`, and `content-type`. Everything else, including PIIGhost headers and
hop-by-hop headers, is dropped.

## Request anonymization (Anthropic field walker)

Anonymize every text-bearing field through the same `ThreadAnonymizationPipeline`:

- `system`: a string, or an array of `{type:"text"}` blocks.
- `messages[].content`: a string, or an array of blocks. Per block type:
  - `text`: anonymize `.text`.
  - `tool_use`: anonymize the string values inside `.input` (the tool-call arguments),
    walking the JSON recursively.
  - `tool_result`: anonymize `.content`, which is a string or an array of `text` blocks.
- `image` / `document` blocks: binary passthrough, never touched.
- `tools[]` tool definitions (`description`, `input_schema`): passthrough. They are static
  schemas; anonymizing them risks breaking them for no gain.

## Response deanonymization (non-streaming)

The response is `{type:"message", content:[...]}`. Deanonymize `content[].text` and
`content[].tool_use.input`. The model can emit a token inside tool-call arguments, and the
real value must be restored so the tool executes against real data. Upstream errors are
relayed verbatim; non-JSON responses are returned raw with their original content type, as
in the OpenAI proxy.

## Streaming (Anthropic SSE)

Anthropic streams a sequence of typed events: `message_start`, `content_block_start`,
`content_block_delta`, `content_block_stop`, `message_delta`, `message_stop`, and `ping`.
Two text streams need restoring, handled differently:

- `content_block_delta` / `text_delta.text`: feed live through an
  `AsyncPlaceholderStreamDecoder` keyed per content-block index. A token split across two
  deltas is reassembled then restored, and text is emitted as it goes for good UX.
- `content_block_delta` / `input_json_delta.partial_json` (streamed tool-call arguments):
  handled inline exactly like `text_delta`, with its own per-index decoder. We restore the
  decoded Python string value of `partial_json`, then re-serialize the event with
  `json.dumps`, so JSON escaping of the restored value is automatic and no buffering to
  `content_block_stop` is needed. A per-index decoder is required so a held token fragment
  never bleeds from one content block into another.

At stream end, every per-index decoder is flushed and any trailing fragment is emitted, as
the OpenAI proxy does. On a well-formed stream those flushes are empty, since the model
completes each token before its block closes; a fragment only appears on a truncated stream
and carries no real value.

All non-text events (`message_start`, `ping`, `message_delta`, usage) are relayed unchanged.

## Thread lifecycle

Claude Code is stateless at the API layer; it resends the whole history each turn. So the
proxy uses an ephemeral thread per request, forgotten at end of response, matching the OpenAI
proxy default. Within one request the pipeline assigns stable tokens over the union of all
messages plus `system`. The reply is deanonymized before it is returned, so Claude Code
stores the real value and the detector re-detects it on the next turn. No cross-turn
determinism is required.

## count_tokens

`POST /anthropic/v1/messages/count_tokens` anonymizes the request the same way, forwards it,
and returns the `{input_tokens}` unchanged. The count reflects the anonymized text, which is
what the model actually sees, so it is the honest number. No deanonymization is needed.

## Validation

### Mocked suite (respx), mirroring `tests/routes/test_openai_chat.py`

Using `ExactMatchDetector({"Patrick": "PERSON"})` with a real `ThreadAnonymizationPipeline`
and a respx-mocked upstream:

- Canonical proof: the forwarded upstream request contains `<<PERSON:1>>` and never
  `Patrick`, and the returned reply is restored to `Patrick`.
- `system` is anonymized.
- `tool_use.input` in an assistant message is restored on the way back.
- `tool_result` content in a user message is anonymized on the way in.
- `image` block is passed through untouched.
- Streaming text with a token split across deltas is reassembled and restored.
- Streaming `tool_use` arguments are restored inline and stay JSON-safe.
- `count_tokens` request is anonymized.
- Default upstream is used when no header is present; `X-PIIGhost-Upstream` overrides it.
- Forwarded headers include `x-api-key` and `anthropic-version`; PIIGhost headers are
  dropped.

### Real run (documented procedure, not CI)

1. `make dev-local` in `piighost-api` to layer an editable `../piighost` on top.
2. Run the API locally with a pipeline whose detector is `ExactMatchDetector` on the tester's
   own first name.
3. Set `ANTHROPIC_BASE_URL=http://localhost:PORT/anthropic` and a real `ANTHROPIC_API_KEY`.
4. In Claude Code, ask for the first letter of that first name.
5. Observe that Claude cannot give the letter (it saw `<<PERSON:1>>`), while the transcript
   restores the real name.

Caveat: subscription OAuth behaves poorly through a custom base URL; use an API key.

## Non-goals (v1)

- Anonymizing tool definitions (`tools[]`).
- Header-driven thread persistence from Claude Code; ephemeral per request is enough.
- Proxy-side exact token counting; delegated to the upstream.
