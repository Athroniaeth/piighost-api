---
icon: lucide/link
---

# Running Claude Code through the proxy

The Anthropic proxy lets Claude Code speak to a real Anthropic-compatible
upstream while piighost de-identifies every request and restores every reply.
Claude Code never sends the real PII to the model.

## Two headers

- `X-PIIGhost-Upstream` (optional): override the upstream base URL per request, for example `https://api.anthropic.com/v1` or a compatible gateway. Absent, the server uses `PIIGHOST_ANTHROPIC_UPSTREAM` (defaults to `https://api.anthropic.com/v1`).
- `X-PIIGhost-Thread-Id` (optional): pin a fixed anonymization thread across requests. Absent, each request uses an ephemeral thread that is forgotten as soon as the reply is returned.

The caller's `x-api-key` or `Authorization: Bearer` header is forwarded as-is to the upstream. The proxy adds no auth of its own on `/anthropic`.

## Routes

| Route | What the proxy does |
|-------|---------------------|
| `POST /anthropic/v1/messages` | anonymizes system, messages, and tool I/O; deanonymizes the reply; streaming supported |
| `POST /anthropic/v1/messages/count_tokens` | anonymizes the request, relays the token count |

An unknown route under `/anthropic/v1` returns 404.

## Point Claude Code at the proxy

```bash
export ANTHROPIC_BASE_URL=http://localhost:8000/anthropic
export ANTHROPIC_API_KEY=sk-ant-...
claude
```

Claude Code picks up `ANTHROPIC_BASE_URL` automatically and routes every call through the proxy.

## The classic proof

1. Start the API with a pipeline whose detector matches your own first name.
   The simplest reproducible setup is an `ExactMatchDetector`, configured in a
   TOML file:

       [detector]
       type = "exact"

       [detector.values]
       Patrick = "PERSON"

   Run the server against that config on a local port, for example 8080.

2. Point Claude Code at the proxy and give it a real key:

       export ANTHROPIC_BASE_URL=http://localhost:8080/anthropic
       export ANTHROPIC_API_KEY=sk-ant-...
       claude

3. In Claude Code, ask for the first letter of that first name, for example
   "What is the first letter of my name, Patrick?".

4. Observe two things. Claude cannot give the letter, because it only ever saw
   `<<PERSON:1>>`, not `Patrick`. Meanwhile the transcript you read is restored,
   so you still see `Patrick`. The model was blind to the PII the whole time.

## Notes

- The upstream defaults to `https://api.anthropic.com/v1`. Override it per
  request with an `X-PIIGhost-Upstream` header, or globally with the
  `PIIGHOST_ANTHROPIC_UPSTREAM` environment variable, to target a gateway.
- Every client header is relayed to the upstream except the hop-by-hop ones,
  `host`, `content-length`, `accept-encoding`, and the `X-PIIGhost-*` control
  headers. This keeps the caller's user-agent and beta flags intact for upstreams
  that validate them.
- A short guidance note is prepended to the system prompt so the model handles
  the placeholder tokens fluently: reuse them verbatim and do not remark that a
  value is masked. Customize it with `PIIGHOST_ANTHROPIC_PLACEHOLDER_NOTE`, or
  disable it by setting that variable to an empty string.
- Each request is a fresh ephemeral thread, matching how Claude Code resends the
  whole history every turn. Pass `X-PIIGhost-Thread-ID` only if you want a
  persistent thread you manage yourself.

## Subscription (OAuth) mode

The base-URL approach above is meant for API-key or gateway usage. A Pro or Max
subscription authenticates with OAuth, and Claude Code hardcodes the real
`api.anthropic.com` for its OAuth and refresh calls, so setting
`ANTHROPIC_BASE_URL` does not route subscription traffic through the proxy: it
either drops you out of subscription mode or fails. Anthropic also validates the
client fingerprint of OAuth requests, so a modifying proxy is not a supported path
for a subscription.

Two knobs help a subscription or fingerprint-sensitive gateway, and the first is
the default because a coding harness is the primary target:

- `PIIGHOST_ANTHROPIC_ANONYMIZE_SYSTEM` (default `false`): by default the system
  prompt is relayed untouched so the upstream can still validate the client. Set
  it to `true` to anonymize the system prompt as well. Message and tool content
  are anonymized either way. If your system prompt can carry PII, set it to `true`.
- Permissive header forwarding (always on for `/anthropic`) keeps the client's
  user-agent and OAuth beta flags, which the upstream may require.

## Known limitations

- Streaming errors surface as a broken stream, not a status. A streaming request commits to HTTP 200 before the upstream stream opens, so an upstream error mid-stream reaches the client as a truncated stream rather than a clean error status. A non-streaming request relays the upstream status faithfully.
- Images and documents are relayed untouched. Multimodal de-identification is out of scope.
- Tool definitions (`tools[]`) are forwarded untouched: the proxy anonymizes only the content that flows through the model, not the schema you define.
