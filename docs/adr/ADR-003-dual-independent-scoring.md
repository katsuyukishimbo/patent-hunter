# ADR-003: Dual independent scoring with Clean Context for Verifier

## Status
Accepted (2026-05-14)

## Context

A patent is "adopted" only when it clears a commercial-viability bar.
A single-LLM judgement is too noisy for this — language models are
known to hallucinate plausible justifications for any score we ask
for, and a single judge gives us no way to detect that.

We needed an architecture that:

1. Catches false positives that any single model would emit.
2. Doesn't degenerate into "both judges quote each other" — i.e. the
   second model must not condition on the first model's answer.
3. Stays cheap enough to run weekly inside a Claude Max subscription.

## Decision

The pipeline scores every patent with **two providers in parallel**
and adopts only patents where **both** independently return
`score >= threshold`:

- Sonnet (via local `claude` CLI subprocess)
- Codex (via `codex exec` subprocess)

The two scorers receive **the exact same patent payload** but are
**never told what the other one said**. This is the
"Clean Context for Verifier" pattern described by Cognition AI's
2026-04 multi-agent essay: a verifier whose context is dirty with
the generator's output is no longer a verifier — it's a rubber stamp.

Concretely, the scorers are invoked with:

```python
await asyncio.gather(
    scorers.sonnet.score_batch(patents, ...),
    scorers.codex.score_batch(patents, ...),
)
```

Each call gets a fresh subprocess. There is no shared session, no
shared cache, no prompt rewriting based on the other model's output.
The runner merges the two streams by `patent_id` only after both
have completed.

## Consequences

Positive:

- False-positive adoptions drop substantially. In the first weekly
  run (2026-W19, BigQuery + Sonnet + Codex) only **4 of 63** fetched
  patents survived the dual filter.
- Provider risk is hedged. If Anthropic or OpenAI ships a model
  regression, the other catches it.
- Each model's per-call confidence (ADR-005 / Phase-2 enhancement)
  is also independent, giving us a second axis of trust.
- Eval harness (P1.5) can report `agreement_rate` as a separate
  metric — divergence between the two judges is a leading indicator
  of prompt drift.

Negative:

- Cost doubles per patent (still well inside the Claude Max + ChatGPT
  Plus quotas in practice).
- Latency is bounded by the slower of the two, not the faster.
- The "Clean Context" rule requires discipline: every future
  enhancement (RAG, retrieval, reranking) must avoid leaking the
  generator's output to the verifier, or the architecture's value
  collapses.

## Alternatives considered

- **Single Sonnet judgement.** Tested briefly during P1 prototyping;
  the model was happy to assign 8/10 to clearly-unsellable patents
  when asked to "be honest".
- **Sonnet generates, then a second Sonnet call critiques.** Same
  underlying model and likely same blind spots; we'd be paying for
  agreement noise rather than disagreement signal.
- **Sequential dual-judge where Codex sees Sonnet's score.** Cheaper
  but defeats the Clean Context principle — Codex's score correlated
  near 1.0 with Sonnet's in our pilot. Not used.

## References

- Cognition AI, *Don't build multi-agents* (2026-04):
  https://cognition.ai/blog/dont-build-multi-agents
- Implementation: `src/patent_hunter/scorers/sonnet.py`,
  `src/patent_hunter/scorers/codex.py`,
  `src/patent_hunter/runner.py` (the merge logic).
- First production run with Clean Context enforced:
  commit `61892f7` (the BigQuery migration also kept the parallel
  pattern intact).
