"""LangGraph construction and run helpers."""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any

try:  # pragma: no cover - exercised when the real dependency is installed.
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph
except ModuleNotFoundError:  # pragma: no cover - covered indirectly in sandbox.
    from .compat import END, START, MemorySaver, StateGraph

from patent_hunter.week import IsoWeek, parse_iso_week

from .nodes import (
    GraphRuntime,
    fetch_node,
    report_node,
    score_codex_node,
    score_sonnet_node,
    verify_node,
)
from .state import PatentHunterState


def initial_state(week: str | IsoWeek) -> PatentHunterState:
    label = week.label if isinstance(week, IsoWeek) else parse_iso_week(week).label
    return {
        "week": label,
        "fetched_patents": [],
        "sonnet_results": [],
        "codex_results": [],
        "scored_patents": [],
        "adopted": [],
        "cost_usd": 0.0,
    }


def graph_thread_id(week: str | IsoWeek) -> str:
    """Return deterministic MemorySaver thread_id for a weekly graph run."""

    label = week.label if isinstance(week, IsoWeek) else parse_iso_week(week).label
    return f"patent-hunter:p2:{label}"


def graph_config(week: str | IsoWeek) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": graph_thread_id(week)}}


def build_state_graph(runtime: GraphRuntime | None = None):
    runtime = runtime or GraphRuntime()
    graph = StateGraph(PatentHunterState)
    graph.add_node("fetch", partial(fetch_node, runtime=runtime))
    graph.add_node("score_sonnet", partial(score_sonnet_node, runtime=runtime))
    graph.add_node("score_codex", partial(score_codex_node, runtime=runtime))
    graph.add_node("verify", partial(verify_node, runtime=runtime))
    graph.add_node("report", partial(report_node, runtime=runtime))

    graph.add_edge(START, "fetch")
    graph.add_edge("fetch", "score_sonnet")
    graph.add_edge("fetch", "score_codex")
    graph.add_edge(["score_sonnet", "score_codex"], "verify")
    graph.add_edge("verify", "report")
    graph.add_edge("report", END)
    return graph


def build_graph(runtime: GraphRuntime | None = None, *, checkpointer: Any | None = None):
    saver = checkpointer if checkpointer is not None else MemorySaver()
    return build_state_graph(runtime).compile(checkpointer=saver)


async def run_graph(
    week: str | IsoWeek, runtime: GraphRuntime | None = None
) -> PatentHunterState:
    app = build_graph(runtime)
    label = week.label if isinstance(week, IsoWeek) else parse_iso_week(week).label
    return await app.ainvoke(initial_state(label), config=graph_config(label))


def dryrun_runtime(
    *,
    out_dir: Path = Path("out"),
    score_threshold: int = 7,
    max_per_category: int = 10,
    top_n: int = 10,
) -> GraphRuntime:
    """Build a runtime from the existing ``scripts/dryrun.py`` fixtures."""

    from scripts.dryrun import (  # local import avoids test-time side effects.
        FIXTURE_PATENTS,
        _FakeAnthropic,
        _fake_codex_runner,
    )

    return GraphRuntime(
        out_dir=out_dir,
        score_threshold=score_threshold,
        max_per_category=max_per_category,
        top_n=top_n,
        fetched_patents=list(FIXTURE_PATENTS),
        sonnet_client=_FakeAnthropic(),
        codex_runner=_fake_codex_runner,
    )
