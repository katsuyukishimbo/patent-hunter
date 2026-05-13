#!/usr/bin/env python3
"""Eval Harness for PatentHunter — Sierra Plan/Build/Review 準拠.

3 回 dryrun を反復実行し、Golden Dataset (cases.json) の期待値と比較。
合意率・期待値一致率・採用率・再現性 (sigma) をメトリクス化し HTML レポート出力。

P1 注意: dryrun は決定論的スタブのため sigma=0 が出る。
本ハーネスは P2 で実 API に切り替えた時に同じインタフェースで動く設計 (再利用可能).

Run with:
    python evals/run_eval.py
"""

from __future__ import annotations

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


def load_cases() -> Dict[str, Dict[str, Any]]:
    raw = json.loads(CASES_PATH.read_text())
    return {c["patent_id"]: c for c in raw["cases"]}


def latest_run_dir() -> Path:
    out_root = ROOT / "out"
    weeks = [p for p in out_root.iterdir() if p.is_dir() and p.name.startswith(("2026-W", "2027-W"))]
    if not weeks:
        raise RuntimeError("no out/<week>/ directory found — run dryrun first")
    return sorted(weeks)[-1]


def run_once() -> List[Dict[str, Any]]:
    """dryrun を 1 回実行し scores.jsonl の各レコードを返す."""
    result = subprocess.run(
        [sys.executable, "scripts/dryrun.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"dryrun failed (rc={result.returncode}):\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    scores_path = latest_run_dir() / "scores.jsonl"
    return [json.loads(line) for line in scores_path.read_text().splitlines() if line.strip()]


def compute_metrics(
    runs: List[List[Dict[str, Any]]], cases: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    """3 回実行結果から per-patent と summary を計算."""
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
    """Bootstrap CDN ベースの 1 ページ HTML."""
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
<title>PatentHunter Eval — {timestamp}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">
<style>
  body {{ padding: 2rem; }}
  .metric-card {{ padding: 1rem; }}
  td small {{ font-size: 0.85rem; }}
</style>
</head>
<body>
<div class="container">
  <h1 class="mb-1">PatentHunter Eval Report</h1>
  <p class="text-muted">Run at {timestamp} · {s['n_runs']} runs × {s['total_patents']} patents</p>

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

  <h2 class="mt-4">P1 制限</h2>
  <p class="text-muted">
    P1 の dryrun は決定論的スタブのため <code>avg_score_sigma</code> は 0 になる。
    本ハーネスの真価は P2 で実 API (Anthropic / Codex CLI) に切り替えた時に発揮される。
    その時点で <strong>同じ cases.json と同じ run_eval.py がそのまま動く設計</strong>。
  </p>
</div>
</body>
</html>
"""
    out_path.write_text(html)


def main() -> None:
    cases = load_cases()
    n_runs = 3
    runs: List[List[Dict[str, Any]]] = []
    for i in range(n_runs):
        print(f"[eval] run {i + 1}/{n_runs} ...", flush=True)
        runs.append(run_once())

    metrics = compute_metrics(runs, cases)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUT_DIR / f"eval_{timestamp}"
    out_path.mkdir(parents=True, exist_ok=True)

    metrics_path = out_path / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False))

    html_path = out_path / "report.html"
    render_html(metrics, html_path, timestamp)

    s = metrics["summary"]
    print()
    print("=== Eval Harness Summary ===")
    print(f"  Agreement rate (Sonnet ⇔ Codex): {s['agreement_rate']:.0%}")
    print(f"  In-range rate                  : {s['in_range_rate']:.0%}")
    print(f"  Status match rate              : {s['status_match_rate']:.0%}")
    print(f"  Avg score sigma                : {s['avg_score_sigma']}")
    print()
    print(f"Metrics: {metrics_path}")
    print(f"Report : {html_path}")


if __name__ == "__main__":
    main()
