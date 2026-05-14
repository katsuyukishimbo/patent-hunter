"""P2 LangGraph wrapper tests."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from patent_hunter import graph
from patent_hunter.graph import (
    GraphRuntime,
    build_graph,
    dryrun_runtime,
    graph_config,
    initial_state,
)
from patent_hunter.models import ScoreResult
from patent_hunter.runner import RunConfig, run
from patent_hunter.scorers.codex import CodexScoreBatch
from patent_hunter.scorers.sonnet import SonnetScoreBatch
from patent_hunter.week import IsoWeek

from tests.conftest import make_patent


def test_state_graph_compiles() -> None:
    app = build_graph(GraphRuntime(fetched_patents=[]))
    mermaid = app.get_graph().draw_mermaid()
    assert "fetch" in mermaid
    assert "score_sonnet" in mermaid
    assert "score_codex" in mermaid
    assert "verify" in mermaid
    assert "report" in mermaid


@pytest.mark.asyncio
async def test_dryrun_end_to_end_populates_state(tmp_path: Path) -> None:
    runtime = dryrun_runtime(out_dir=tmp_path)
    app = build_graph(runtime)

    state = await app.ainvoke(initial_state("2026-W19"), config=graph_config("2026-W19"))

    assert state["week"] == "2026-W19"
    assert len(state["fetched_patents"]) == 4
    assert len(state["sonnet_results"]) == 4
    assert len(state["codex_results"]) == 4
    assert len(state["scored_patents"]) == 4
    assert len(state["adopted"]) == 3
    assert state["cost_usd"] > 0
    assert Path(state["report_paths"]["scores"]).exists()


@pytest.mark.asyncio
async def test_graph_min_confidence_filters_dryrun_adoptions(tmp_path: Path) -> None:
    runtime = dryrun_runtime(out_dir=tmp_path, min_confidence=80)
    app = build_graph(runtime)

    state = await app.ainvoke(initial_state("2026-W19"), config=graph_config("2026-W19"))

    assert len(state["adopted"]) == 1
    assert state["adopted"][0].patent.patent_id == "8234811"


@pytest.mark.asyncio
async def test_graph_report_node_sends_discord_notification(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = []

    async def fake_notify_top_patents(**kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(
        "patent_hunter.graph.nodes._notify_top_patents",
        fake_notify_top_patents,
    )

    runtime = dryrun_runtime(
        out_dir=tmp_path,
        discord_webhook_url="https://discord.com/api/webhooks/1/token",
    )
    app = build_graph(runtime)

    await app.ainvoke(initial_state("2026-W19"), config=graph_config("2026-W19"))

    assert len(calls) == 1
    assert calls[0]["webhook_url"] == "https://discord.com/api/webhooks/1/token"
    assert calls[0]["week_label"] == "2026-W19"
    assert len([sp for sp in calls[0]["scored"] if sp.adopted]) == 3


@pytest.mark.asyncio
async def test_scorer_nodes_run_in_parallel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events: list[tuple[str, float]] = []

    async def fake_sonnet(patents, *, client=None, model=None):
        events.append(("sonnet_start", time.perf_counter()))
        await asyncio.sleep(0.05)
        events.append(("sonnet_end", time.perf_counter()))
        return SonnetScoreBatch(
            results=[
                ScoreResult(patent_id=p.patent_id, model="sonnet", score=8)
                for p in patents
            ],
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.01,
        )

    async def fake_codex(
        patents, *, runner=None, sandbox=None, codex_bin=None, timeout=600.0
    ):
        events.append(("codex_start", time.perf_counter()))
        await asyncio.sleep(0.05)
        events.append(("codex_end", time.perf_counter()))
        return CodexScoreBatch(
            results=[
                ScoreResult(patent_id=p.patent_id, model="codex", score=8)
                for p in patents
            ],
            invocations=1,
            cost_usd_estimate=0.30,
        )

    monkeypatch.setattr(graph.scorer_sonnet, "score_batch", fake_sonnet)
    monkeypatch.setattr(graph.scorer_codex, "score_batch", fake_codex)

    runtime = GraphRuntime(out_dir=tmp_path, fetched_patents=[make_patent("A")])
    app = build_graph(runtime)
    state = await app.ainvoke(initial_state("2026-W19"), config=graph_config("2026-W19"))

    times = dict(events)
    assert times["sonnet_start"] < times["codex_end"]
    assert times["codex_start"] < times["sonnet_end"]
    assert len(state["adopted"]) == 1


def test_graph_dryrun_scores_match_existing_runner(tmp_path: Path) -> None:
    from scripts.dryrun import (
        FIXTURE_PATENTS,
        _fake_codex_runner,
        _fake_sonnet_runner,
    )

    week = IsoWeek(2026, 19)
    graph_out = tmp_path / "graph"
    runner_out = tmp_path / "runner"

    graph_state = asyncio.run(
        build_graph(dryrun_runtime(out_dir=graph_out)).ainvoke(
            initial_state(week), config=graph_config(week)
        )
    )
    runner_paths = run(
        RunConfig(
            week=week,
            out_dir=runner_out,
            score_threshold=7,
            max_per_category=10,
            top_n=10,
            fetched_patents=list(FIXTURE_PATENTS),
            sonnet_client=_fake_sonnet_runner,
            codex_runner=_fake_codex_runner,
        )
    )

    graph_rows = [
        json.loads(line)
        for line in Path(graph_state["report_paths"]["scores"]).read_text().splitlines()
    ]
    runner_rows = [
        json.loads(line) for line in runner_paths["scores"].read_text().splitlines()
    ]
    assert graph_rows == runner_rows
