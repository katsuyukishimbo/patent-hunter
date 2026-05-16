#!/usr/bin/env python3
"""Best-effort issue auto-triage commenter for GitHub Actions."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

GH_TIMEOUT_SECONDS = 30
SIMILARITY_THRESHOLD = 0.3
SIMILAR_LIMIT = 5
OPEN_LOOKBACK_DAYS = 90
STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "for",
    "in",
    "on",
    "is",
}


def _run_gh_json(args: list[str]) -> Any:
    proc = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        timeout=GH_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        raise RuntimeError(f"gh {' '.join(args)} failed: {stderr}")
    return json.loads(proc.stdout or "null")


def _run_gh_comment(issue_number: int, body: str) -> None:
    proc = subprocess.run(
        ["gh", "issue", "comment", str(issue_number), "--body", body],
        capture_output=True,
        text=True,
        timeout=GH_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        raise RuntimeError(f"gh issue comment failed: {stderr}")


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9_]+", text.lower())
    return {w for w in words if w and w not in STOPWORDS}


def _jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _filter_recent_open_issues(rows: Any, issue_number: int) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=OPEN_LOOKBACK_DAYS)
    filtered: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        number = row.get("number")
        title = row.get("title")
        created_at = row.get("createdAt")
        if not isinstance(number, int) or number == issue_number:
            continue
        if not isinstance(title, str) or not isinstance(created_at, str):
            continue
        try:
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if created < cutoff:
            continue
        filtered.append(row)
    return filtered


def _find_similar_issues(
    current_title: str,
    open_issues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    current_tokens = _tokenize(current_title)
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in open_issues:
        title = row.get("title")
        if not isinstance(title, str):
            continue
        score = _jaccard(current_tokens, _tokenize(title))
        if score >= SIMILARITY_THRESHOLD:
            scored.append((score, row))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    output: list[dict[str, Any]] = []
    for score, row in scored[:SIMILAR_LIMIT]:
        output.append(
            {
                "number": row["number"],
                "title": row["title"],
                "similarity": round(score, 3),
            }
        )
    return output


def _list_adr_files(root: Path) -> list[str]:
    adr_dir = root / "docs" / "adr"
    if not adr_dir.exists() or not adr_dir.is_dir():
        return []
    names = [name for name in os.listdir(adr_dir) if name.endswith(".md")]
    return sorted(names)


def _list_code_files(root: Path) -> list[Path]:
    base = root / "src" / "patent_hunter"
    if not base.exists():
        return []
    return sorted(path for path in base.rglob("*.py") if path.is_file())


def _select_relevant_code_files(
    issue_title: str, issue_body: str, code_paths: list[Path]
) -> list[str]:
    keywords = [tok for tok in _tokenize(f"{issue_title} {issue_body}") if len(tok) >= 4]
    if not keywords:
        return [str(path) for path in code_paths[:10]]

    scored: list[tuple[int, str]] = []
    for path in code_paths:
        as_posix = path.as_posix().lower()
        score = 0
        for kw in keywords:
            if kw in as_posix:
                score += 3
        if score == 0:
            try:
                text = path.read_text(encoding="utf-8").lower()
            except OSError:
                text = ""
            for kw in keywords:
                if kw in text:
                    score += 1
        if score > 0:
            scored.append((score, str(path)))
    scored.sort(key=lambda item: item[0], reverse=True)
    if scored:
        return [path for _, path in scored[:20]]
    return [str(path) for path in code_paths[:10]]


def _extract_anthropic_text(response: Any) -> str:
    blocks = getattr(response, "content", None)
    if blocks is None and isinstance(response, dict):
        blocks = response.get("content")
    if not isinstance(blocks, list):
        return ""

    parts: list[str] = []
    for block in blocks:
        if isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            continue
        block_type = getattr(block, "type", None)
        if block_type == "text":
            parts.append(str(getattr(block, "text", "")))
    return "\n".join(part for part in parts if part.strip()).strip()


def _build_prompt(
    issue_title: str,
    issue_body: str,
    similar: list[dict[str, Any]],
    adr_files: list[str],
    relevant_code_files: list[str],
) -> str:
    similar_lines = "\n".join(
        f"- #{item['number']} {item['title']} (jaccard={item['similarity']})"
        for item in similar
    )
    if not similar_lines:
        similar_lines = "- none above threshold"

    adr_lines = "\n".join(f"- {name}" for name in adr_files) if adr_files else "- none"
    code_lines = (
        "\n".join(f"- {path}" for path in relevant_code_files)
        if relevant_code_files
        else "- none"
    )
    return f"""You are triaging a newly opened GitHub issue for Patent Hunter.

Issue title:
{issue_title}

Issue body:
{issue_body}

Similar open issues from the last 90 days (Jaccard >= 0.3):
{similar_lines}

ADR files under docs/adr:
{adr_lines}

Relevant code paths under src/patent_hunter:
{code_lines}

Write a triage comment in markdown.
Must include:
1) priority estimate,
2) similar past issues with references,
3) relevant ADR / code paths,
4) suggested first action.
Keep it <=300 words."""


def _generate_triage_comment(prompt: str) -> str | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[auto_triage] warning: ANTHROPIC_API_KEY is not set; skipping", file=sys.stderr)
        return None
    try:
        import anthropic
    except Exception as exc:  # pragma: no cover - runtime-only path
        print(f"[auto_triage] warning: anthropic import failed: {exc}", file=sys.stderr)
        return None

    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    text = _extract_anthropic_text(response)
    return text or None


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    try:
        if len(argv) < 1:
            print("[auto_triage] warning: issue_number argument is required", file=sys.stderr)
            return 0

        if shutil.which("gh") is None:
            print("[auto_triage] warning: gh CLI not found; skipping", file=sys.stderr)
            return 0

        issue_number = int(argv[0])
        root = Path(__file__).resolve().parents[1]

        issue = _run_gh_json(
            ["issue", "view", str(issue_number), "--json", "title,body,labels"]
        )
        if not isinstance(issue, dict):
            print("[auto_triage] warning: failed to parse issue payload", file=sys.stderr)
            return 0
        issue_title = str(issue.get("title", ""))
        issue_body = str(issue.get("body", ""))

        open_rows = _run_gh_json(
            [
                "issue",
                "list",
                "--state",
                "open",
                "--json",
                "number,title,createdAt",
                "--limit",
                "50",
            ]
        )
        open_issues = _filter_recent_open_issues(open_rows, issue_number)
        similar = _find_similar_issues(issue_title, open_issues)

        adr_files = _list_adr_files(root)
        code_paths = _list_code_files(root)
        relevant_code_files = _select_relevant_code_files(
            issue_title, issue_body, code_paths
        )

        prompt = _build_prompt(
            issue_title=issue_title,
            issue_body=issue_body,
            similar=similar,
            adr_files=adr_files,
            relevant_code_files=relevant_code_files,
        )
        comment = _generate_triage_comment(prompt)
        if not comment:
            return 0

        _run_gh_comment(issue_number, comment)
        print(f"[auto_triage] posted triage comment to issue #{issue_number}")
        return 0
    except Exception as exc:  # pragma: no cover - best-effort global guard
        print(f"[auto_triage] warning: {exc}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
