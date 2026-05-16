#!/usr/bin/env python3
"""Best-effort autonomous anomaly issue creation for Patent Hunter."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

GH_TIMEOUT_SECONDS = 30
RECENT_DUPLICATE_DAYS = 30
WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")


def _coerce_scalar(raw: str) -> Any:
    value = raw.strip()
    if value == "":
        return value
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_run_log(path: Path) -> dict[str, Any]:
    """Parse ``run.log`` lines written as ``key=value``."""
    if not path.exists():
        return {}

    parsed: dict[str, Any] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("  "):
            continue
        if "=" not in line:
            continue
        key, raw = line.split("=", 1)
        parsed[key.strip()] = _coerce_scalar(raw)
    return parsed


def _split_week_label(label: str) -> tuple[int, int]:
    year_s, week_s = label.split("-W")
    year = int(year_s)
    week = int(week_s)
    date.fromisocalendar(year, week, 1)
    return year, week


def previous_weeks(base_week: str, count: int) -> list[str]:
    """Return ``count`` ISO week labels ending at ``base_week`` (oldest first)."""
    year, week = _split_week_label(base_week)
    base = date.fromisocalendar(year, week, 1)
    labels: list[str] = []
    for i in range(count - 1, -1, -1):
        d_i = base - timedelta(weeks=i)
        iso = d_i.isocalendar()
        labels.append(f"{iso.year}-W{iso.week:02d}")
    return labels


def detect_latest_week(out_dir: Path) -> str | None:
    labels: list[str] = []
    if not out_dir.exists():
        return None
    for p in out_dir.iterdir():
        if not p.is_dir() or not WEEK_RE.match(p.name):
            continue
        try:
            _split_week_label(p.name)
        except ValueError:
            continue
        labels.append(p.name)
    if not labels:
        return None
    return max(labels, key=lambda w: date.fromisocalendar(*_split_week_label(w), 1))


def detect_adoption_dry(weeks_data: list[tuple[str, dict[str, Any]]]) -> dict[str, Any] | None:
    """Detect 3-week zero-adoption streak."""
    if len(weeks_data) < 3:
        return None
    tail = weeks_data[-3:]
    rows: list[tuple[str, int]] = []
    for week, data in tail:
        adopted = _as_int(data.get("adopted"))
        if adopted is None:
            return None
        rows.append((week, adopted))
    if all(adopted == 0 for _, adopted in rows):
        return {
            "type": "adoption_dry",
            "priority": "high",
            "detected_week": tail[-1][0],
            "weeks": [week for week, _ in rows],
            "adopted": rows,
        }
    return None


def detect_scorer_failures(latest_data: dict[str, Any], latest_week: str) -> dict[str, Any] | None:
    """Detect Sonnet/Codex scoring failures in the latest week."""
    sonnet_errors = _as_int(latest_data.get("sonnet_errors")) or 0
    codex_errors = _as_int(latest_data.get("codex_errors")) or 0
    if sonnet_errors <= 0 and codex_errors <= 0:
        return None
    return {
        "type": "scorer_failures",
        "priority": "high",
        "detected_week": latest_week,
        "sonnet_errors": sonnet_errors,
        "codex_errors": codex_errors,
    }


def detect_cost_spike(weeks_data: list[tuple[str, dict[str, Any]]]) -> dict[str, Any] | None:
    """Detect +50% week-over-week total cost jump."""
    if len(weeks_data) < 2:
        return None
    prev_week, prev_data = weeks_data[-2]
    latest_week, latest_data = weeks_data[-1]

    prev_cost = _as_float(prev_data.get("total_cost_usd"))
    latest_cost = _as_float(latest_data.get("total_cost_usd"))
    if prev_cost is None or latest_cost is None:
        return None
    if prev_cost <= 0:
        return None

    change = (latest_cost - prev_cost) / prev_cost
    if change <= 0.5:
        return None
    return {
        "type": "cost_spike",
        "priority": "medium",
        "detected_week": latest_week,
        "previous_week": prev_week,
        "previous_cost_usd": round(prev_cost, 4),
        "latest_cost_usd": round(latest_cost, 4),
        "change_ratio": change,
    }


def _load_metrics(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _summary_composite(summary: dict[str, Any]) -> float | None:
    if "composite" in summary:
        return _as_float(summary.get("composite"))

    agreement = _as_float(summary.get("agreement_rate"))
    in_range = _as_float(summary.get("in_range_rate"))
    status = _as_float(summary.get("status_match_rate"))
    if agreement is None or in_range is None or status is None:
        return None
    return agreement * 0.4 + in_range * 0.3 + status * 0.3


def detect_eval_regression(evals_dir: Path) -> dict[str, Any] | None:
    """Detect composite drop >=0.1 across latest two eval_live runs."""
    if not evals_dir.exists():
        return None

    runs = sorted(
        p for p in evals_dir.iterdir() if p.is_dir() and p.name.startswith("eval_live_")
    )
    if len(runs) < 2:
        return None

    prev_dir, latest_dir = runs[-2], runs[-1]
    prev_metrics = _load_metrics(prev_dir / "metrics.json")
    latest_metrics = _load_metrics(latest_dir / "metrics.json")
    if not prev_metrics or not latest_metrics:
        return None

    prev_summary = prev_metrics.get("summary")
    latest_summary = latest_metrics.get("summary")
    if not isinstance(prev_summary, dict) or not isinstance(latest_summary, dict):
        return None

    prev_comp = _summary_composite(prev_summary)
    latest_comp = _summary_composite(latest_summary)
    if prev_comp is None or latest_comp is None:
        return None

    delta = latest_comp - prev_comp
    if delta > -0.1:
        return None

    return {
        "type": "eval_regression",
        "priority": "high",
        "detected_week": "n/a",
        "previous_eval": prev_dir.name,
        "latest_eval": latest_dir.name,
        "previous_composite": round(prev_comp, 4),
        "latest_composite": round(latest_comp, 4),
        "delta": round(delta, 4),
    }


def build_issue_title(anomaly: dict[str, Any]) -> str:
    kind = anomaly["type"]
    if kind == "adoption_dry":
        w0, w2 = anomaly["weeks"][0], anomaly["weeks"][-1]
        return f"[auto-anomaly] adoption_dry: 3 weeks zero adopted ({w0} ~ {w2})"
    if kind == "scorer_failures":
        return (
            "[auto-anomaly] scorer_failures: "
            f"sonnet_errors={anomaly['sonnet_errors']} codex_errors={anomaly['codex_errors']}"
        )
    if kind == "cost_spike":
        pct = int(round(anomaly["change_ratio"] * 100))
        return (
            "[auto-anomaly] cost_spike: "
            f"+{pct}% total_cost_usd ({anomaly['previous_week']} -> {anomaly['detected_week']})"
        )
    if kind == "eval_regression":
        return (
            "[auto-anomaly] eval_regression: "
            f"composite dropped {anomaly['delta']:+.3f} ({anomaly['previous_eval']} -> {anomaly['latest_eval']})"
        )
    return f"[auto-anomaly] {kind}"


def build_issue_body(anomaly: dict[str, Any]) -> str:
    kind = anomaly["type"]
    detected_week = anomaly.get("detected_week", "n/a")
    priority = anomaly["priority"]

    if kind == "adoption_dry":
        lines = [
            f"**Anomaly type:** {kind}",
            f"**Priority:** {priority}",
            f"**Detected at week:** {detected_week}",
            "",
            "## Details",
            (
                "3 consecutive weeks with zero adopted patents "
                f"({', '.join(anomaly['weeks'])})."
            ),
            "",
            "## Data",
        ]
        for week, adopted in anomaly["adopted"]:
            lines.append(f"- {week}: adopted={adopted}")
        lines += [
            "",
            "## Suggested next steps",
            f"- Inspect `out/{detected_week}/run.log` and `out/{detected_week}/events.jsonl`",
            "- Re-run `python3 evals/run_eval_live.py` to check scorer regression",
            "- Review recent prompt changes in `src/patent_hunter/scorers/prompts.py`",
        ]
    elif kind == "scorer_failures":
        lines = [
            f"**Anomaly type:** {kind}",
            f"**Priority:** {priority}",
            f"**Detected at week:** {detected_week}",
            "",
            "## Details",
            "Scorer errors were reported in the latest run.",
            "",
            "## Data",
            f"- {detected_week}: sonnet_errors={anomaly['sonnet_errors']}",
            f"- {detected_week}: codex_errors={anomaly['codex_errors']}",
            "",
            "## Suggested next steps",
            f"- Inspect `out/{detected_week}/run.log` for stack traces",
            "- Re-run `python3 scripts/dryrun.py --live` to isolate scorer failures",
            "- Check CLI auth/session health for Claude/Codex subprocesses",
        ]
    elif kind == "cost_spike":
        lines = [
            f"**Anomaly type:** {kind}",
            f"**Priority:** {priority}",
            f"**Detected at week:** {detected_week}",
            "",
            "## Details",
            (
                "Weekly cost increased more than 50% compared with the previous week "
                f"({anomaly['previous_week']} -> {detected_week})."
            ),
            "",
            "## Data",
            f"- {anomaly['previous_week']}: total_cost_usd={anomaly['previous_cost_usd']}",
            f"- {detected_week}: total_cost_usd={anomaly['latest_cost_usd']}",
            f"- change: {anomaly['change_ratio'] * 100:.1f}%",
            "",
            "## Suggested next steps",
            f"- Inspect `out/{detected_week}/run.log` for token/cost drivers",
            "- Compare fetched/scored counts between the two weeks",
            "- Consider tightening `--max-per-category` or `--max-cost`",
        ]
    else:
        lines = [
            f"**Anomaly type:** {kind}",
            f"**Priority:** {priority}",
            f"**Detected at week:** {detected_week}",
            "",
            "## Details",
            "Eval live composite metric regressed by >= 0.1.",
            "",
            "## Data",
            f"- {anomaly['previous_eval']}: composite={anomaly['previous_composite']}",
            f"- {anomaly['latest_eval']}: composite={anomaly['latest_composite']}",
            f"- delta: {anomaly['delta']}",
            "",
            "## Suggested next steps",
            f"- Inspect `evals/out/{anomaly['latest_eval']}/metrics.json`",
            "- Re-run `python3 evals/run_eval_live.py` for confirmation",
            "- Review recent prompt and threshold changes under `src/patent_hunter/`",
        ]

    lines += ["", "---", "Auto-generated by `scripts/auto_issue.py`."]
    return "\n".join(lines)


def issue_exists_recently(
    title: str,
    days: int = RECENT_DUPLICATE_DAYS,
    *,
    gh_bin: str = "gh",
) -> bool:
    """Check if matching issue title exists within the last ``days`` days."""
    cmd = [
        gh_bin,
        "issue",
        "list",
        "--search",
        title,
        "--state",
        "all",
        "--limit",
        "5",
        "--json",
        "number,title,createdAt",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"[auto_issue] warning: duplicate lookup failed: {exc}")
        return False

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        print(f"[auto_issue] warning: duplicate lookup failed: {stderr}")
        return False

    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return False
    if not isinstance(rows, list):
        return False

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("title") != title:
            continue
        created_at = row.get("createdAt")
        if not isinstance(created_at, str):
            continue
        try:
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if created >= cutoff:
            return True
    return False


def create_issue(
    title: str,
    body: str,
    labels: list[str],
    *,
    gh_bin: str = "gh",
) -> str | None:
    """Create one issue and return its URL when available."""
    cmd = [gh_bin, "issue", "create", "--title", title, "--body", body]
    for label in labels:
        cmd.extend(["--label", label])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"[auto_issue] warning: issue creation failed: {exc}")
        return None

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        print(f"[auto_issue] warning: issue creation failed: {stderr}")
        return None

    out = (proc.stdout or "").strip()
    if not out:
        return None
    return out.splitlines()[-1].strip()


def _resolve_base_week(base_week_arg: str | None, out_dir: Path) -> str | None:
    if base_week_arg:
        return base_week_arg
    return detect_latest_week(out_dir)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create anomaly issues from recent Patent Hunter out/<week>/ logs "
            "(best-effort; failures never break cron)."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print issue payloads without creating GitHub issues.",
    )
    parser.add_argument(
        "--week",
        default=None,
        help="Base ISO week label (default: latest under out/, e.g. 2026-W20).",
    )
    parser.add_argument(
        "--out-dir",
        default="out",
        help="Root directory containing out/<week>/ runs.",
    )
    parser.add_argument(
        "--evals-dir",
        default="evals/out",
        help="Root directory containing eval_live_* outputs.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    out_dir = Path(args.out_dir)
    evals_dir = Path(args.evals_dir)

    try:
        base_week = _resolve_base_week(args.week, out_dir)
        if base_week is None:
            print("[auto_issue] no anomalies")
            return 0

        weeks = previous_weeks(base_week, 4)
    except ValueError as exc:
        print(f"[auto_issue] warning: bad week input: {exc}")
        return 0

    print(f"[auto_issue] scanning weeks: {', '.join(weeks)}")

    weeks_data: list[tuple[str, dict[str, Any]]] = []
    for week in weeks:
        run_data = parse_run_log(out_dir / week / "run.log")
        weeks_data.append((week, run_data))

    anomalies: list[dict[str, Any]] = []
    latest_week, latest_data = weeks_data[-1]
    for anomaly in [
        detect_adoption_dry(weeks_data),
        detect_scorer_failures(latest_data, latest_week),
        detect_cost_spike(weeks_data),
        detect_eval_regression(evals_dir),
    ]:
        if anomaly:
            anomalies.append(anomaly)

    if not anomalies:
        print("[auto_issue] no anomalies")
        return 0

    noun = "anomaly" if len(anomalies) == 1 else "anomalies"
    print(f"[auto_issue] detected: {len(anomalies)} {noun}")
    for anomaly in anomalies:
        print(
            f"  - {anomaly['type']} ({anomaly['priority']}): "
            f"{build_issue_title(anomaly).split(': ', 1)[1]}"
        )

    if args.dry_run:
        for anomaly in anomalies:
            title = build_issue_title(anomaly)
            body = build_issue_body(anomaly)
            labels = ["auto-issue", "anomaly", f"priority:{anomaly['priority']}"]
            print("[DRY] would create issue:")
            print(f"  title: {title}")
            print(f"  labels: {','.join(labels)}")
            print("  body: |")
            for line in body.splitlines():
                print(f"    {line}")
        return 0

    gh_bin = shutil.which("gh")
    if gh_bin is None:
        print("[auto_issue] gh CLI not found in PATH; skipping (best-effort)")
        return 0

    for anomaly in anomalies:
        title = build_issue_title(anomaly)
        body = build_issue_body(anomaly)
        labels = ["auto-issue", "anomaly", f"priority:{anomaly['priority']}"]

        if issue_exists_recently(title, gh_bin=gh_bin):
            print(f"[auto_issue] skip duplicate issue within 30 days: {title}")
            continue

        url = create_issue(title, body, labels, gh_bin=gh_bin)
        if url:
            print(f"[auto_issue] created issue: {url}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
