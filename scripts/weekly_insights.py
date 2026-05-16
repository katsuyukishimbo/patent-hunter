#!/usr/bin/env python3
"""Best-effort weekly insight issue creation via Claude CLI."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import auto_issue

CLAUDE_TIMEOUT_SECONDS = 300
CLAUDE_SYSTEM_PROMPT = """You are reviewing 4 weeks of operational data from "Patent Hunter", a
weekly LLM-judged patent triage pipeline. The pipeline scores expired US
utility patents with two independent models and adopts those scoring >=7
from both.

Read the events and metrics, then return strictly JSON of the form:

{
  "observations": [string, ...],
  "hypotheses": [
    {"title": string,
     "rationale": string,
     "concrete_change": string},
    {...}, {...}
  ]
}

- "observations": 2-5 succinct patterns you notice (cost trends,
  adoption rate, errors, agreement, anything notable).
- "hypotheses": EXACTLY 3 improvement hypotheses. Each must propose a
  concrete change to either prompts, thresholds, or pipeline structure.
- "title" must be <=60 chars and start with an imperative verb.
- "rationale" must cite specific weeks / numbers from the data.
- "concrete_change" must reference a file path under src/ or evals/.

Output JSON only. No prose around it."""


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _last_run_done_event(events_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    run_done: list[dict[str, Any]] = [
        row for row in events_rows if row.get("event") == "run_done"
    ]
    if not run_done:
        return None
    return run_done[-1]


def _agreement_rate_from_scores(scores_rows: list[dict[str, Any]]) -> float | None:
    if not scores_rows:
        return None
    total = 0
    agree = 0
    for row in scores_rows:
        if not isinstance(row, dict):
            continue
        sonnet = row.get("sonnet")
        codex = row.get("codex")
        if not isinstance(sonnet, dict) or not isinstance(codex, dict):
            continue
        sonnet_score = _safe_float(sonnet.get("score"))
        codex_score = _safe_float(codex.get("score"))
        if sonnet_score is None or codex_score is None:
            continue
        sonnet_ge = sonnet_score >= 7
        codex_ge = codex_score >= 7
        total += 1
        if sonnet_ge == codex_ge:
            agree += 1
    if total == 0:
        return None
    return round(agree / total, 3)


def build_week_summary(week: str, out_dir: Path) -> dict[str, Any]:
    week_dir = out_dir / week
    run_log = auto_issue.parse_run_log(week_dir / "run.log")
    events_rows = _read_jsonl(week_dir / "events.jsonl")
    scores_rows = _read_jsonl(week_dir / "scores.jsonl")
    run_done = _last_run_done_event(events_rows) or {}

    adopted = _safe_int(run_log.get("adopted"))
    if adopted is None:
        adopted = _safe_int(run_done.get("adopted"))
    sonnet_errors = _safe_int(run_log.get("sonnet_errors"))
    codex_errors = _safe_int(run_log.get("codex_errors"))
    total_cost = _safe_float(run_log.get("total_cost_usd"))
    if total_cost is None:
        total_cost = _safe_float(run_done.get("total_cost_usd"))

    sonnet_in = _safe_int(run_log.get("sonnet_input_tokens")) or 0
    sonnet_out = _safe_int(run_log.get("sonnet_output_tokens")) or 0
    agreement_rate = _agreement_rate_from_scores(scores_rows)

    return {
        "week": week,
        "adopted": adopted if adopted is not None else 0,
        "sonnet_errors": sonnet_errors if sonnet_errors is not None else 0,
        "codex_errors": codex_errors if codex_errors is not None else 0,
        "total_cost_usd": round(total_cost, 4) if total_cost is not None else 0.0,
        "sonnet_tokens": sonnet_in + sonnet_out,
        "agreement_rate": agreement_rate,
    }


def _mock_insights(weeks_summary: list[dict[str, Any]]) -> dict[str, Any]:
    first = weeks_summary[0]
    last = weeks_summary[-1]
    return {
        "observations": [
            (
                "Adoption changed from "
                f"{first['adopted']} ({first['week']}) to {last['adopted']} ({last['week']})."
            ),
            (
                "Latest week errors: "
                f"sonnet={last['sonnet_errors']} codex={last['codex_errors']}."
            ),
            (
                "Latest weekly cost is "
                f"${last['total_cost_usd']:.4f} with sonnet_tokens={last['sonnet_tokens']}."
            ),
        ],
        "hypotheses": [
            {
                "title": "Tighten adoption threshold for low-confidence weeks",
                "rationale": (
                    f"{last['week']} shows adoption={last['adopted']} and "
                    f"errors={last['sonnet_errors'] + last['codex_errors']}."
                ),
                "concrete_change": (
                    "Adjust threshold logic in `src/patent_hunter/runner.py` "
                    "to gate adoption when scorer error counts rise."
                ),
            },
            {
                "title": "Add scorer retry telemetry to eval regression loop",
                "rationale": (
                    f"Cost moved from ${first['total_cost_usd']:.4f} in {first['week']} "
                    f"to ${last['total_cost_usd']:.4f} in {last['week']}."
                ),
                "concrete_change": (
                    "Extend `evals/run_eval_live.py` output with retry counters "
                    "sourced from `out/<week>/events.jsonl`."
                ),
            },
            {
                "title": "Refine prompt guardrails for disagreement cases",
                "rationale": (
                    f"Agreement rate is {last['agreement_rate']} in {last['week']} "
                    "for the latest scored set."
                ),
                "concrete_change": (
                    "Update disagreement handling guidance in "
                    "`src/patent_hunter/scorers/prompts.py`."
                ),
            },
        ],
    }


def _parse_claude_stdout(stdout: str) -> dict[str, Any]:
    top = json.loads(stdout.strip())
    if not isinstance(top, dict):
        raise ValueError("Claude JSON envelope must be an object")
    result = top.get("result")
    if not isinstance(result, str):
        raise ValueError("Claude JSON envelope missing string 'result'")
    inner = json.loads(result)
    if not isinstance(inner, dict):
        raise ValueError("Claude inner payload must be an object")
    return inner


def _invoke_claude(weeks_summary: list[dict[str, Any]]) -> dict[str, Any] | None:
    payload = json.dumps(weeks_summary, ensure_ascii=False, indent=2)
    full_prompt = (
        CLAUDE_SYSTEM_PROMPT
        + "\n\nData (JSON):\n"
        + payload
    )
    argv = [
        os.environ.get("CLAUDE_BIN", "claude"),
        "-p",
        full_prompt,
        "--output-format=json",
    ]
    try:
        proc = subprocess.run(
            argv,
            cwd="/tmp",
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        print(f"[weekly_insights] warning: Claude CLI not found: {exc}")
        return None
    except subprocess.TimeoutExpired as exc:
        print(f"[weekly_insights] warning: Claude CLI timed out: {exc}")
        return None

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        print(
            f"[weekly_insights] warning: Claude CLI failed rc={proc.returncode}: {stderr}"
        )
        return None

    try:
        return _parse_claude_stdout(proc.stdout or "")
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"[weekly_insights] warning: invalid Claude JSON: {exc}")
        return None


def _build_issue_body(
    observations: list[str], hypothesis: dict[str, Any]
) -> str:
    lines = ["## Observations (last 4 weeks)"]
    for obs in observations:
        lines.append(f"- {obs}")
    lines += [
        "",
        "## Hypothesis",
        f"**Rationale:** {hypothesis.get('rationale', '')}",
        "",
        f"**Concrete change:** {hypothesis.get('concrete_change', '')}",
        "",
        "---",
        "Auto-generated by `scripts/weekly_insights.py`.",
    ]
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create weekly improvement-hypothesis issues from the latest 4 weeks "
            "of Patent Hunter operational data."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print issue payloads only (no Claude/gh calls).",
    )
    parser.add_argument(
        "--week",
        default=None,
        help="Base ISO week label (default: latest under out/).",
    )
    parser.add_argument(
        "--out-dir",
        default="out",
        help="Root directory containing out/<week>/ runs.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    out_dir = Path(args.out_dir)
    base_week = args.week or auto_issue.detect_latest_week(out_dir)
    if base_week is None:
        print("[weekly_insights] no week directories found; skipping")
        return 0

    try:
        weeks = auto_issue.previous_weeks(base_week, 4)
    except ValueError as exc:
        print(f"[weekly_insights] warning: bad week input: {exc}")
        return 0

    print(f"[weekly_insights] scanning weeks: {', '.join(weeks)}")

    weeks_summary = [build_week_summary(week, out_dir) for week in weeks]
    if args.dry_run:
        insights = _mock_insights(weeks_summary)
    else:
        insights = _invoke_claude(weeks_summary)
        if insights is None:
            return 0

    observations_raw = insights.get("observations")
    hypotheses_raw = insights.get("hypotheses")
    if not isinstance(observations_raw, list) or not isinstance(hypotheses_raw, list):
        print("[weekly_insights] warning: Claude payload missing observations/hypotheses")
        return 0

    observations = [str(obs) for obs in observations_raw][:5]
    hypotheses = [
        hyp for hyp in hypotheses_raw if isinstance(hyp, dict)
    ][:3]
    if len(hypotheses) != 3:
        print("[weekly_insights] warning: expected exactly 3 hypotheses; skipping")
        return 0

    if args.dry_run:
        for hypothesis in hypotheses:
            title_short = str(hypothesis.get("title", "")).strip()[:60]
            full_title = f"[auto-insight] {title_short}"
            body = _build_issue_body(observations, hypothesis)
            print("[DRY] would create issue:")
            print(f"  title: {full_title}")
            print("  labels: auto-issue,insight")
            print("  body: |")
            for line in body.splitlines():
                print(f"    {line}")
        return 0

    gh_bin = shutil.which("gh")
    if gh_bin is None:
        print("[weekly_insights] warning: gh CLI not found in PATH; skipping")
        return 0

    for hypothesis in hypotheses:
        title_short = str(hypothesis.get("title", "")).strip()[:60]
        if not title_short:
            continue
        full_title = f"[auto-insight] {title_short}"
        body = _build_issue_body(observations, hypothesis)

        if auto_issue.issue_exists_recently(full_title, gh_bin=gh_bin):
            print(f"[weekly_insights] skip duplicate issue within 30 days: {full_title}")
            continue

        created = auto_issue.create_issue(
            full_title,
            body,
            ["auto-issue", "insight"],
            gh_bin=gh_bin,
        )
        if created:
            print(f"[weekly_insights] created issue: {created}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
