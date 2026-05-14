# ADR-002: Claude CLI subprocess invoked with `cwd=/tmp`

## Status
Accepted (2026-05-14)

## Context

The Sonnet judge of the dual-model scorer needs to call Claude. Two
realistic transports exist:

1. **Anthropic Python SDK** with a dedicated `ANTHROPIC_API_KEY`.
2. **The local `claude` CLI (Claude Code)** as a subprocess.

Option 1 makes for cleaner Python code but requires the operator to
pay per-token usage on top of an existing Claude Max subscription —
i.e. double-billing for the same model access.

Option 2 reuses the Max subscription and has no incremental cost,
but introduces a hidden trap that is invisible from the code.

### The trap

When `claude` is invoked from inside the Patent Hunter project
directory, Claude Code automatically ingests:

- `CLAUDE.md` (~60 lines)
- `.claude/rules/*.md` (>1,000 lines combined)
- the project's `memory/` directory
- any configured agents and skills

The total prompt context grows to ~88,000 tokens, which Claude Code
sends as `cache_creation_input_tokens` on the very first call. We
measured this directly:

| `cwd` of subprocess | `cache_creation_input_tokens` | per-call cost (USD-equivalent) |
|---|---:|---:|
| project root | 87,873 | $0.55 |
| `/tmp`        |      0 | $0.013 |

That is a **~40x** difference. A weekly run of 50 patents at $0.55
per call would burn $27 of Claude Max quota per batch and $108 per
month for a single scorer. With `cwd=/tmp` the same batch costs
about $0.65, comfortably inside the subscription.

## Decision

The Sonnet scorer always spawns

```bash
claude -p <prompt> --output-format=json
```

with `cwd="/tmp"`, regardless of where the caller invoked the runner
from. The behaviour is enforced inside
`src/patent_hunter/scorers/sonnet.py` via
`asyncio.create_subprocess_exec(..., cwd="/tmp")` and is not a
runtime option.

## Consequences

Positive:

- ~40x reduction in per-call quota consumption versus the naive
  invocation.
- A weekly run stays comfortably within the Claude Max usage cap.
- Behaviour is reproducible across users: nobody's local rules /
  skills / memory leak into the scorer's prompt context.

Negative:

- Claude Code's project-specific skills (e.g. content-create,
  brand-voice) are unavailable to the scorer. This is acceptable —
  the scorer only needs the explicit prompt the runner gives it.
- A power user who wants to inject their own skills must wrap
  `claude` in a CLI shim and point `CLAUDE_BIN` at the shim. The
  scorer respects the env var.

## Alternatives considered

- **Use the Anthropic Python SDK with an API key.** Cleaner code,
  but introduces a separate billing surface and forces the operator
  to manage two sets of credentials.
- **Run `claude` with the default `cwd`.** Rejected after the cost
  measurement above; 40x cost regression is not acceptable.
- **Disable Claude Code's auto-discovery via a flag.** No such flag
  exists in 2.1.x at the time of writing. Setting `cwd=/tmp` is the
  documented escape hatch.

## References

- Cost measurements: original transcripts in
  `out/2026-W19/run.log` and the project changelog around 2026-05-14.
- Anthropic Skills auto-discovery (the source of the bloat):
  https://docs.anthropic.com/claude/code/skills
- Implementation: `src/patent_hunter/scorers/sonnet.py`
  (see the `CLAUDE_SUBPROCESS_CWD` constant).
