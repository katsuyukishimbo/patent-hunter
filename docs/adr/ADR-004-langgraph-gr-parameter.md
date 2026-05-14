# ADR-004: Graph nodes accept config via `gr=` (not `runtime=`)

## Status
Accepted (2026-05-14)

## Context

The LangGraph nodes (`fetch`, `score_sonnet`, `score_codex`,
`verify`, `report`) need access to a config object that holds
out_dir, score_threshold, vintage_years, max_cost, and the optional
test fixtures.

The natural Python idiom is to bind the config at graph construction
time via `functools.partial`:

```python
graph.add_node("fetch", partial(fetch_node, runtime=runtime))
```

The first implementation used the keyword name `runtime`. It worked
on a recent LangGraph snapshot during prototyping. Then a fresh
`pip install -e ".[dev]"` pulled in **LangGraph 1.2.0**, the BigQuery
migration's graph tests started failing with:

```
AttributeError: 'Runtime' object has no attribute 'fetched_patents'
```

despite the production CLI still passing pytest moments earlier.

## The trap

LangGraph 1.x reserves the keyword **`runtime`** for an injected
framework-level `Runtime` object (its own context primitive). When
a node function declares a `runtime` kwarg, LangGraph passes its own
`Runtime` and **silently overrides** the `partial(..., runtime=...)`
bind. Our `GraphRuntime` dataclass never made it to the node body.

The failure mode is particularly nasty:

- pytest with mocked LangGraph stub (`compat.py`) passes.
- The CLI works in development before `pip install` pulls the new
  version.
- Production breakage only surfaces after a dependency refresh.

## Decision

All graph nodes take their user-side config via the keyword **`gr`**
(short for "graph runtime"):

```python
async def fetch_node(
    state: PatentHunterState, *, gr: GraphRuntime | None = None
) -> PatentHunterState:
    ...

graph.add_node("fetch", partial(fetch_node, gr=runtime))
```

The signature is documented with a docstring note explaining the
reason for the otherwise-cryptic two-letter name.

## Consequences

Positive:

- No collision with LangGraph's reserved injection. Future minor
  version bumps of LangGraph cannot accidentally hijack our config.
- The bug is recorded as a real-world example of "framework
  metaprogramming overriding user code", which is useful in
  interviews when someone asks about debugging a regression.
- Tests pinned to LangGraph 1.x can rely on the framework-provided
  `Runtime` if we ever want it later — the keyword space is free.

Negative:

- `gr` is less self-documenting than `runtime`. The docstring in
  every node calls out the reason so a future reader doesn't rename
  it back blindly.
- If LangGraph ever reserves `gr` too (extremely unlikely), we'd
  rename again. Cost: a single rename across `nodes.py` and
  `build.py`.

## Alternatives considered

- **Use `runtime=` and call `gr = runtime or GraphRuntime()` inside
  the node.** Doesn't work — by the time the node runs, the user-side
  `runtime` argument has been replaced by LangGraph's own object.
- **Pass the config through `state` instead of a kwarg.** Pollutes
  the checkpointed state with non-serialisable Python objects
  (logging clients, scorer mocks, paths) and would invalidate
  MemorySaver / Postgres checkpoints on every config change.
- **Move config to a module-level singleton.** Breaks test
  isolation; parallel runs with different out_dirs become impossible.

## References

- LangGraph 1.x Runtime injection:
  https://langchain-ai.github.io/langgraph/reference/runtime/
- The failing test that uncovered it:
  `tests/graph/test_graph.py::test_dryrun_end_to_end_populates_state`
- Commit fixing the regression: `884b0a4` (also fixes the launchd
  PATH issue, but the bulk of the diff is the rename).
- Implementation: `src/patent_hunter/graph/nodes.py`,
  `src/patent_hunter/graph/build.py`.
