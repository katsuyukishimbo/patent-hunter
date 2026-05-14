"""End-to-end runner test with both scorers stubbed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from patent_hunter.runner import CostBudgetExceededError, RunConfig, run
from patent_hunter.week import IsoWeek

from tests.conftest import make_patent


def _patent(pid: str, score_pair: tuple[int, int]):
    """Helper: a patent + the (sonnet, codex) scores we want stubs to return."""
    p = make_patent(pid)
    p._stub_scores = score_pair  # type: ignore[attr-defined]
    return p


def _extract_payload(text):
    """Pull the JSON array out of the user-message string."""
    marker = "PATENTS = "
    idx = text.find(marker)
    assert idx >= 0
    return json.loads(text[idx + len(marker) :])


def _build_sonnet_runner(scores_by_id):
    async def runner(argv, timeout):
        prompt = argv[argv.index("-p") + 1]
        ids = [obj["patent_id"] for obj in _extract_payload(prompt)]
        out = [
            {
                "patent_id": pid,
                "plain_english": f"Plain for {pid}",
                "consumer_viable": True,
                "bom_estimate": "$1-2",
                "amazon_gap": True,
                "review_signal": "noise",
                "score": scores_by_id[pid][0],
            }
            for pid in ids
        ]
        return json.dumps(
            {
                "type": "result",
                "is_error": False,
                "result": json.dumps(out),
                "total_cost_usd": 0.01,
                "usage": {"input_tokens": 100, "output_tokens": 50},
            }
        )

    return runner


def _build_sonnet_runner_with_missing(scores_by_id, missing_ids: set[str]):
    async def runner(argv, timeout):
        prompt = argv[argv.index("-p") + 1]
        ids = [obj["patent_id"] for obj in _extract_payload(prompt)]
        out = [
            {
                "patent_id": pid,
                "plain_english": f"Plain for {pid}",
                "consumer_viable": True,
                "bom_estimate": "$1-2",
                "amazon_gap": True,
                "review_signal": "noise",
                "score": scores_by_id[pid][0],
            }
            for pid in ids
            if pid not in missing_ids
        ]
        return json.dumps(
            {
                "type": "result",
                "is_error": False,
                "result": json.dumps(out),
                "total_cost_usd": 0.01,
                "usage": {"input_tokens": 100, "output_tokens": 50},
            }
        )

    return runner


def _build_codex_runner(scores_by_id):
    async def runner(argv, timeout):
        # The prompt is the last arg.
        prompt = argv[-1]
        ids = [obj["patent_id"] for obj in _extract_payload(prompt)]
        return json.dumps(
            [
                {
                    "patent_id": pid,
                    "plain_english": f"Codex view of {pid}",
                    "consumer_viable": True,
                    "bom_estimate": "$1-2",
                    "amazon_gap": False,
                    "review_signal": "x",
                    "score": scores_by_id[pid][1],
                }
                for pid in ids
            ]
        )

    return runner


def test_runner_adopts_only_when_both_models_pass(tmp_path: Path):
    week = IsoWeek(2026, 19)
    patents = [
        _patent("A", (8, 8)),  # adopted
        _patent("B", (9, 5)),  # rejected (codex low)
        _patent("C", (4, 9)),  # rejected (sonnet low)
        _patent("D", (7, 7)),  # adopted (boundary)
    ]
    scores_by_id = {p.patent_id: p._stub_scores for p in patents}  # type: ignore[attr-defined]

    cfg = RunConfig(
        week=week,
        out_dir=tmp_path,
        score_threshold=7,
        fetched_patents=patents,
        sonnet_client=_build_sonnet_runner(scores_by_id),
        codex_runner=_build_codex_runner(scores_by_id),
    )
    paths = run(cfg)

    log_text = paths["log"].read_text()
    assert "fetched=4" in log_text
    assert "adopted=2" in log_text

    # scores.jsonl has one row per scored patent
    rows = [json.loads(l) for l in paths["scores"].read_text().splitlines()]
    assert len(rows) == 4
    adopted_ids = {r["patent"]["patent_id"] for r in rows if r["adopted"]}
    assert adopted_ids == {"A", "D"}

    html = paths["report"].read_text()
    assert "Patent Hunter" in html
    assert "ADOPTED" in html
    assert "US A" in html or "USA" in html  # link present


def test_runner_handles_zero_fetched(tmp_path: Path):
    week = IsoWeek(2026, 19)
    cfg = RunConfig(
        week=week,
        out_dir=tmp_path,
        fetched_patents=[],
        sonnet_client=_build_sonnet_runner({}),
        codex_runner=_build_codex_runner({}),
    )
    paths = run(cfg)
    assert paths["report"].exists()
    log_text = paths["log"].read_text()
    assert "fetched=0" in log_text
    assert "adopted=0" in log_text


def test_runner_shows_shortlist_when_nothing_adopted(tmp_path: Path):
    week = IsoWeek(2026, 19)
    patents = [_patent("A", (3, 3)), _patent("B", (4, 5))]
    scores_by_id = {p.patent_id: p._stub_scores for p in patents}  # type: ignore[attr-defined]
    cfg = RunConfig(
        week=week,
        out_dir=tmp_path,
        score_threshold=7,
        fetched_patents=patents,
        sonnet_client=_build_sonnet_runner(scores_by_id),
        codex_runner=_build_codex_runner(scores_by_id),
    )
    paths = run(cfg)
    html = paths["report"].read_text()
    # No ADOPTED badge, but the shortlist rows must still be rendered.
    assert "shortlist" in html
    assert "ADOPTED" not in html


def test_runner_allows_partial_score_failures(tmp_path: Path):
    week = IsoWeek(2026, 19)
    patents = [_patent(f"P{i:02d}", (8, 8)) for i in range(50)]
    scores_by_id = {p.patent_id: p._stub_scores for p in patents}  # type: ignore[attr-defined]
    missing_ids = {f"P{i:02d}" for i in range(5)}

    cfg = RunConfig(
        week=week,
        out_dir=tmp_path,
        score_threshold=7,
        fetched_patents=patents,
        sonnet_client=_build_sonnet_runner_with_missing(scores_by_id, missing_ids),
        codex_runner=_build_codex_runner(scores_by_id),
    )
    paths = run(cfg)

    log_text = paths["log"].read_text()
    assert "fetched=50" in log_text
    assert "scored=50" in log_text
    assert "adopted=45" in log_text
    assert "sonnet_errors=5" in log_text
    assert "partial_score_errors" in log_text

    rows = [json.loads(l) for l in paths["scores"].read_text().splitlines()]
    assert sum(1 for row in rows if row["adopted"]) == 45
    assert sum(1 for row in rows if row["sonnet"]["error"]) == 5


def test_runner_budget_exceeded_writes_partial_outputs(tmp_path: Path):
    week = IsoWeek(2026, 19)
    patents = [_patent("A", (8, 8))]
    scores_by_id = {p.patent_id: p._stub_scores for p in patents}  # type: ignore[attr-defined]
    cfg = RunConfig(
        week=week,
        out_dir=tmp_path,
        score_threshold=7,
        fetched_patents=patents,
        sonnet_client=_build_sonnet_runner(scores_by_id),
        codex_runner=_build_codex_runner(scores_by_id),
        max_cost_usd=0.0001,
    )

    with pytest.raises(CostBudgetExceededError) as excinfo:
        run(cfg)

    paths = excinfo.value.output_paths
    assert paths["report"].exists()
    assert paths["scores"].exists()
    assert paths["log"].exists()
    assert "budget_exceeded" in paths["log"].read_text()
    events = [json.loads(line) for line in paths["events"].read_text().splitlines()]
    assert "budget_exceeded" in {row["event"] for row in events}
