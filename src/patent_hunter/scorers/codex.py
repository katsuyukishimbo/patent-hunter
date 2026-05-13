"""Codex (GPT-5.x) scorer via the `codex exec` subprocess.

Clean Context for Verifier (Cognition 3 principles): the Codex prompt
does NOT include the Sonnet output. The two models score independently
and the runner only accepts patents where BOTH score >= threshold.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
from dataclasses import dataclass
from typing import List, Optional

from .prompts import SYSTEM_PROMPT, build_user_payload
from .json_extract import extract_json_array
from ..models import Patent, ScoreResult

logger = logging.getLogger(__name__)

# Rough cost estimate for run.log only. xhigh effort makes pricing volatile;
# we keep a *per-batch* flat estimate so the user can sanity-check spend.
CODEX_COST_USD_PER_BATCH_ESTIMATE = 0.30


@dataclass
class CodexScoreBatch:
    results: List[ScoreResult]
    invocations: int
    cost_usd_estimate: float


def _build_command(prompt: str, *, sandbox: str, codex_bin: str) -> List[str]:
    """Build the argv for `codex exec`.

    We pass the prompt as a single positional argument. `codex exec`
    expects either stdin or a positional; the positional is simpler.
    """
    return [
        codex_bin,
        "exec",
        f"--sandbox={sandbox}",
        "--skip-git-repo-check",
        prompt,
    ]


def _result_from_json(obj: dict, patent_id: str, raw_text: str) -> ScoreResult:
    score_raw = obj.get("score", 0)
    try:
        score = int(score_raw)
    except (TypeError, ValueError):
        score = 0
    return ScoreResult(
        patent_id=str(obj.get("patent_id") or patent_id),
        model="codex",
        plain_english=str(obj.get("plain_english") or ""),
        consumer_viable=_optional_bool(obj.get("consumer_viable")),
        bom_estimate=str(obj.get("bom_estimate") or ""),
        amazon_gap=_optional_bool(obj.get("amazon_gap")),
        review_signal=str(obj.get("review_signal") or ""),
        score=max(0, min(10, score)),
        raw=raw_text,
    )


def _optional_bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        lo = v.strip().lower()
        if lo in {"true", "yes", "y", "1"}:
            return True
        if lo in {"false", "no", "n", "0"}:
            return False
    return None


async def _run_codex(argv: List[str], timeout: float) -> str:
    """Run codex exec and return its stdout. Raises on non-zero exit."""
    logger.debug("Codex argv: %s", " ".join(shlex.quote(a) for a in argv))
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode != 0:
        raise RuntimeError(
            f"codex exec failed (rc={proc.returncode}): "
            f"{(stderr_b or b'').decode('utf-8', 'replace')[:500]}"
        )
    return (stdout_b or b"").decode("utf-8", "replace")


async def score_batch(
    patents: List[Patent],
    *,
    runner=None,
    sandbox: Optional[str] = None,
    codex_bin: Optional[str] = None,
    timeout: float = 600.0,
) -> CodexScoreBatch:
    """Score one batch with Codex.

    `runner` is an optional async callable(argv, timeout)->stdout used by
    tests to bypass subprocess. Default uses `_run_codex`.
    """
    if not patents:
        return CodexScoreBatch(results=[], invocations=0, cost_usd_estimate=0.0)

    runner = runner or _run_codex
    sandbox = sandbox or os.environ.get("CODEX_SANDBOX", "workspace-write")
    codex_bin = codex_bin or os.environ.get("CODEX_BIN", "codex")

    user_text = build_user_payload(patents)
    full_prompt = f"{SYSTEM_PROMPT}\n\n---\n\n{user_text}"
    argv = _build_command(full_prompt, sandbox=sandbox, codex_bin=codex_bin)

    logger.info("Codex: scoring batch of %d patents", len(patents))
    try:
        stdout = await runner(argv, timeout)
    except (asyncio.TimeoutError, RuntimeError, FileNotFoundError) as exc:
        logger.warning("Codex invocation failed: %s", exc)
        return CodexScoreBatch(
            results=[
                ScoreResult(
                    patent_id=p.patent_id,
                    model="codex",
                    raw="",
                    error=f"invocation_error: {exc}",
                )
                for p in patents
            ],
            invocations=1,
            cost_usd_estimate=0.0,
        )

    by_id = {p.patent_id: p for p in patents}
    results: List[ScoreResult] = []
    try:
        items = extract_json_array(stdout)
    except ValueError as exc:
        logger.warning("Codex returned non-JSON output: %s", exc)
        return CodexScoreBatch(
            results=[
                ScoreResult(
                    patent_id=p.patent_id,
                    model="codex",
                    raw=stdout,
                    error=f"json_parse_error: {exc}",
                )
                for p in patents
            ],
            invocations=1,
            cost_usd_estimate=CODEX_COST_USD_PER_BATCH_ESTIMATE,
        )

    seen: set[str] = set()
    for obj in items:
        if not isinstance(obj, dict):
            continue
        pid = str(obj.get("patent_id") or "")
        if pid not in by_id:
            continue
        results.append(_result_from_json(obj, pid, stdout))
        seen.add(pid)
    for p in patents:
        if p.patent_id not in seen:
            results.append(
                ScoreResult(
                    patent_id=p.patent_id,
                    model="codex",
                    raw=stdout,
                    error="missing_from_batch_response",
                )
            )

    return CodexScoreBatch(
        results=results,
        invocations=1,
        cost_usd_estimate=CODEX_COST_USD_PER_BATCH_ESTIMATE,
    )
