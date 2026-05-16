#!/usr/bin/env python3
"""LIVE Eval Harness for PatentHunter — real Claude CLI + Codex CLI.

This is the live-mode sibling of ``evals/run_eval.py``. Where ``run_eval.py``
exercises the deterministic fixture scorers (so ``avg_score_sigma`` is always
0), this script invokes ``scripts/dryrun.py --live``, which routes through the
real Claude CLI and Codex CLI subprocesses.

It is the entry point for autoresearch-style prompt improvement loops:

1. Edit ``src/patent_hunter/scorers/prompts.py``.
2. Run ``python evals/run_eval_live.py`` (3 runs against the same 4 fixtures).
3. Keep the prompt change only if Sierra metrics (in-range, status-match,
   agreement, sigma) improve.

The Golden Dataset (``evals/cases.json``) and the metric computation are
intentionally shared with ``run_eval.py``; we copy the small set of pure
functions inline rather than extracting a module so the two harnesses stay
independently readable (YAGNI — extract later if a third caller appears).

Outputs go to ``evals/out/eval_live_<timestamp>/`` so live runs do not
overwrite the cheap fixture-mode runs.

Run with:
    python evals/run_eval_live.py
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = Path(__file__).parent / "cases.json"
OUT_DIR = Path(__file__).parent / "out"

# Each live batch invokes Claude CLI + Codex CLI in parallel for 4 patents.
# In practice we observe 60-180s per `run_once()`; 600s leaves headroom for
# Codex xhigh reasoning and retry backoff (1s + 2s) without false timeouts.
LIVE_SUBPROCESS_TIMEOUT_SECONDS = 600


def load_cases() -> Dict[str, Dict[str, Any]]:
    raw = json.loads(CASES_PATH.read_text())
    return {c["patent_id"]: c for c in raw["cases"]}


def latest_run_dir() -> Path:
    out_root = ROOT / "out"
    weeks = [
        p
        for p in out_root.iterdir()
        if p.is_dir() and p.name.startswith(("2026-W", "2027-W"))
    ]
    if not weeks:
        raise RuntimeError("no out/<week>/ directory found — run dryrun --live first")
    return sorted(weeks)[-1]


def run_once() -> List[Dict[str, Any]]:
    """Run ``dryrun.py --live`` once and return scores.jsonl as parsed records."""
    result = subprocess.run(
        [sys.executable, "scripts/dryrun.py", "--live"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=LIVE_SUBPROCESS_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        stderr_tail = "\n".join(result.stderr.splitlines()[-50:])
        raise RuntimeError(
            "dryrun --live failed "
            f"(rc={result.returncode}):\n"
            f"stdout={result.stdout}\n"
            f"stderr_tail={stderr_tail}"
        )
    scores_path = latest_run_dir() / "scores.jsonl"
    return [
        json.loads(line) for line in scores_path.read_text().splitlines() if line.strip()
    ]


def compute_metrics(
    runs: List[List[Dict[str, Any]]], cases: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    """Mirror of run_eval.compute_metrics — kept inline for harness independence."""
    per_patent: Dict[str, Dict[str, Any]] = {}

    patent_ids = {entry["patent"]["patent_id"] for run in runs for entry in run}
    for pid in patent_ids:
        sonnet_scores: List[int] = []
        codex_scores: List[int] = []
        consensus_scores: List[float] = []
        adopted_flags: List[bool] = []
        for run in runs:
            for entry in run:
                if entry["patent"]["patent_id"] == pid:
                    sonnet_scores.append(entry["sonnet"]["score"])
                    codex_scores.append(entry["codex"]["score"])
                    consensus_scores.append(entry["consensus_score"])
                    adopted_flags.append(entry["adopted"])
                    break

        case = cases.get(pid, {})
        expected_range = case.get("expected_score_range", [0, 10])
        expected_status = case.get("expected_status", "unknown")
        actual_status = "adopted" if all(adopted_flags) else "reject"
        consensus_mean = statistics.mean(consensus_scores)

        per_patent[pid] = {
            "title": case.get("title", ""),
            "sonnet_scores": sonnet_scores,
            "codex_scores": codex_scores,
            "sonnet_mean": round(statistics.mean(sonnet_scores), 2),
            "codex_mean": round(statistics.mean(codex_scores), 2),
            "sonnet_sigma": round(statistics.pstdev(sonnet_scores), 3),
            "codex_sigma": round(statistics.pstdev(codex_scores), 3),
            "consensus_mean": round(consensus_mean, 2),
            "actual_status": actual_status,
            "expected_range": expected_range,
            "expected_status": expected_status,
            "in_range": expected_range[0] <= consensus_mean <= expected_range[1],
            "status_match": actual_status == expected_status,
            "notes": case.get("notes", ""),
        }

    total = len(per_patent)
    in_range_count = sum(1 for m in per_patent.values() if m["in_range"])
    status_match_count = sum(1 for m in per_patent.values() if m["status_match"])

    agreement_total = 0
    agreement_count = 0
    for run in runs:
        for entry in run:
            agreement_total += 1
            sonnet_ge = entry["sonnet"]["score"] >= 7
            codex_ge = entry["codex"]["score"] >= 7
            if sonnet_ge == codex_ge:
                agreement_count += 1

    avg_sigma = (
        statistics.mean([m["sonnet_sigma"] + m["codex_sigma"] for m in per_patent.values()]) / 2
        if per_patent
        else 0.0
    )

    return {
        "summary": {
            "total_patents": total,
            "n_runs": len(runs),
            "in_range_rate": round(in_range_count / total, 3) if total else 0.0,
            "status_match_rate": round(status_match_count / total, 3) if total else 0.0,
            "agreement_rate": round(agreement_count / agreement_total, 3)
            if agreement_total
            else 0.0,
            "avg_score_sigma": round(avg_sigma, 4),
        },
        "per_patent": per_patent,
    }


def render_html(metrics: Dict[str, Any], out_path: Path, timestamp: str) -> None:
    """Render the LIVE-mode HTML report (Sierra Plan/Build/Review layout)."""
    s = metrics["summary"]
    rows = []
    for pid, m in sorted(metrics["per_patent"].items()):
        in_range_badge = (
            '<span class="badge bg-success">in-range</span>'
            if m["in_range"]
            else '<span class="badge bg-warning text-dark">off</span>'
        )
        status_badge = (
            '<span class="badge bg-success">match</span>'
            if m["status_match"]
            else '<span class="badge bg-danger">mismatch</span>'
        )
        rows.append(
            f"""
        <tr>
          <td><code>{pid}</code><br><small class="text-muted">{m['title']}</small></td>
          <td>{m['sonnet_mean']} <small class="text-muted">±{m['sonnet_sigma']}</small></td>
          <td>{m['codex_mean']} <small class="text-muted">±{m['codex_sigma']}</small></td>
          <td><strong>{m['consensus_mean']}</strong></td>
          <td>{m['expected_range'][0]}-{m['expected_range'][1]} {in_range_badge}</td>
          <td>{m['actual_status']} → {m['expected_status']} {status_badge}</td>
          <td><small>{m['notes']}</small></td>
        </tr>"""
        )

    rows_html = "".join(rows)
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>PatentHunter Eval (LIVE) — {timestamp}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">
<style>
  body {{ padding: 2rem; }}
  .metric-card {{ padding: 1rem; }}
  td small {{ font-size: 0.85rem; }}
</style>
</head>
<body>
<div class="container">
  <h1 class="mb-1">PatentHunter Eval Report (LIVE)</h1>
  <p class="text-muted">Run at {timestamp} · {s['n_runs']} runs × {s['total_patents']} patents</p>
  <p><small>LIVE mode: 実 Claude CLI + Codex CLI を呼んだ結果</small></p>

  <h2 class="mt-4">Summary (Sierra Plan/Build/Review)</h2>
  <div class="row g-2">
    <div class="col-md-3"><div class="card metric-card"><strong>Agreement rate</strong><br><span class="fs-3">{s['agreement_rate']:.0%}</span><br><small class="text-muted">Sonnet ⇔ Codex</small></div></div>
    <div class="col-md-3"><div class="card metric-card"><strong>In-range rate</strong><br><span class="fs-3">{s['in_range_rate']:.0%}</span><br><small class="text-muted">score within expected</small></div></div>
    <div class="col-md-3"><div class="card metric-card"><strong>Status match rate</strong><br><span class="fs-3">{s['status_match_rate']:.0%}</span><br><small class="text-muted">adopted/reject 一致</small></div></div>
    <div class="col-md-3"><div class="card metric-card"><strong>Avg score sigma</strong><br><span class="fs-3">{s['avg_score_sigma']}</span><br><small class="text-muted">across {s['n_runs']} runs</small></div></div>
  </div>

  <h2 class="mt-4">Per-Patent</h2>
  <table class="table table-striped table-hover">
    <thead>
      <tr>
        <th>Patent</th>
        <th>Sonnet mean</th>
        <th>Codex mean</th>
        <th>Consensus</th>
        <th>Expected range</th>
        <th>Status (actual → expected)</th>
        <th>Notes</th>
      </tr>
    </thead>
    <tbody>{rows_html}
    </tbody>
  </table>

  <h2 class="mt-4">Anthropic 4-stage Maturity</h2>
  <ul>
    <li><strong>Stage 1</strong> — Smoke test (動く): ✅</li>
    <li><strong>Stage 2</strong> — Golden dataset (期待値): ✅ cases.json で固定</li>
    <li><strong>Stage 3</strong> — Regression (退化検出): ✅ in_range / status_match で alert</li>
    <li><strong>Stage 4</strong> — Continuous eval (CI 統合): P2.5 で GitHub Actions 連携予定</li>
  </ul>
</div>
</body>
</html>
"""
    out_path.write_text(html)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the LIVE eval harness (real Claude CLI + Codex CLI) "
            "3 times against the golden dataset and emit a Sierra-style report."
        )
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Number of full live runs to average over (default: 3).",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    cases = load_cases()
    n_runs = args.runs
    runs: List[List[Dict[str, Any]]] = []
    for i in range(n_runs):
        print(f"[eval-live] run {i + 1}/{n_runs} ...", flush=True)
        runs.append(run_once())

    metrics = compute_metrics(runs, cases)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUT_DIR / f"eval_live_{timestamp}"
    out_path.mkdir(parents=True, exist_ok=True)

    metrics_path = out_path / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False))

    html_path = out_path / "report.html"
    render_html(metrics, html_path, timestamp)

    s = metrics["summary"]
    print()
    print("=== Eval Harness Summary (LIVE) ===")
    print(f"  Agreement rate (Sonnet ⇔ Codex): {s['agreement_rate']:.0%}")
    print(f"  In-range rate                  : {s['in_range_rate']:.0%}")
    print(f"  Status match rate              : {s['status_match_rate']:.0%}")
    print(f"  Avg score sigma                : {s['avg_score_sigma']}")
    print()
    print(f"Metrics: {metrics_path}")
    print(f"Report : {html_path}")


if __name__ == "__main__":
    main()
