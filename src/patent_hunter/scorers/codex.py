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
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional

from .prompts import SYSTEM_PROMPT, build_user_payload
from .json_extract import extract_json_array, score_result_kwargs
from ..models import Patent, ScoreResult
from ..observability import emit

logger = logging.getLogger(__name__)

# Rough cost estimate for run.log only. xhigh effort makes pricing volatile;
# we keep a *per-batch* flat estimate so the user can sanity-check spend.
CODEX_COST_USD_PER_BATCH_ESTIMATE = 0.30
MAX_SCORE_ATTEMPTS = 3
SCORE_RETRY_BASE_DELAY_SECONDS = 1.0
SCORE_RETRY_FACTOR = 2.0

RunnerOutput = str | tuple[str, str, int]
Runner = Callable[[List[str], float], Awaitable[RunnerOutput]]


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
    return ScoreResult(model="codex", **score_result_kwargs(obj, patent_id, raw_text))


async def _run_codex(argv: List[str], timeout: float) -> tuple[str, str, int]:
    """Run codex exec and return stdout, stderr, and return code."""
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
    return (
        (stdout_b or b"").decode("utf-8", "replace"),
        (stderr_b or b"").decode("utf-8", "replace"),
        int(proc.returncode or 0),
    )


async def _invoke_runner(
    runner: Runner,
    argv: List[str],
    timeout: float,
) -> tuple[str, str, int]:
    out = await runner(argv, timeout)
    if isinstance(out, tuple):
        stdout, stderr, returncode = out
        return stdout, stderr, returncode
    return out, "", 0


def _retry_delay(attempt: int) -> float:
    return SCORE_RETRY_BASE_DELAY_SECONDS * (SCORE_RETRY_FACTOR ** (attempt - 1))


async def _invoke_with_retries(
    runner: Runner,
    argv: List[str],
    timeout: float,
) -> tuple[str, str, int]:
    last_error: BaseException | None = None
    reason = "unknown"
    for attempt in range(1, MAX_SCORE_ATTEMPTS + 1):
        try:
            stdout, stderr, returncode = await _invoke_runner(runner, argv, timeout)
        except FileNotFoundError:
            raise
        except asyncio.TimeoutError as exc:
            last_error = exc
            reason = "timeout"
        except RuntimeError as exc:
            last_error = exc
            reason = str(exc)[:500]
        else:
            if returncode == 0:
                return stdout, stderr, returncode
            reason = f"returncode={returncode}"
            last_error = RuntimeError(reason)
            if attempt >= MAX_SCORE_ATTEMPTS:
                return stdout, stderr, returncode

        if attempt >= MAX_SCORE_ATTEMPTS:
            if last_error is not None:
                raise last_error
            raise RuntimeError(reason)

        delay = _retry_delay(attempt)
        emit(
            "score_retry",
            level="warn",
            model="codex",
            attempt=attempt + 1,
            reason=reason,
        )
        logger.warning(
            "Codex transient failure (%s), retrying attempt %d/%d in %.1fs",
            reason,
            attempt + 1,
            MAX_SCORE_ATTEMPTS,
            delay,
        )
        await asyncio.sleep(delay)

    raise RuntimeError("unreachable retry state")


def _error_results(
    patents: List[Patent], *, error: str, raw: str = ""
) -> List[ScoreResult]:
    return [
        ScoreResult(
            patent_id=p.patent_id,
            model="codex",
            raw=raw,
            error=error,
        )
        for p in patents
    ]


def _emit_score_done(
    *,
    results: List[ScoreResult],
    invocations: int,
    cost_usd_estimate: float,
    started: float,
) -> None:
    emit(
        "score_done",
        model="codex",
        results=len(results),
        errors=sum(1 for r in results if r.error),
        duration_ms=int((time.perf_counter() - started) * 1000),
        cost_usd=round(cost_usd_estimate, 4),
        invocations=invocations,
    )


async def score_batch(
    patents: List[Patent],
    *,
    runner: Runner | None = None,
    sandbox: Optional[str] = None,
    codex_bin: Optional[str] = None,
    timeout: float = 600.0,
) -> CodexScoreBatch:
    """Score one batch with Codex.

    `runner` is an optional async callable(argv, timeout)->stdout used by
    tests to bypass subprocess. Default uses `_run_codex`.
    """
    started = time.perf_counter()
    emit("score_started", model="codex", batch_size=len(patents))
    if not patents:
        _emit_score_done(
            results=[],
            invocations=0,
            cost_usd_estimate=0.0,
            started=started,
        )
        return CodexScoreBatch(results=[], invocations=0, cost_usd_estimate=0.0)

    runner = runner or _run_codex
    sandbox = sandbox or os.environ.get("CODEX_SANDBOX", "workspace-write")
    codex_bin = codex_bin or os.environ.get("CODEX_BIN", "codex")

    user_text = build_user_payload(patents)
    full_prompt = f"{SYSTEM_PROMPT}\n\n---\n\n{user_text}"
    argv = _build_command(full_prompt, sandbox=sandbox, codex_bin=codex_bin)

    logger.info("Codex: scoring batch of %d patents", len(patents))
    try:
        stdout, stderr, returncode = await _invoke_with_retries(runner, argv, timeout)
    except (asyncio.TimeoutError, RuntimeError, FileNotFoundError) as exc:
        logger.warning("Codex invocation failed: %s", exc)
        out = CodexScoreBatch(
            results=_error_results(patents, error=f"invocation_error: {exc}"),
            invocations=1,
            cost_usd_estimate=0.0,
        )
        _emit_score_done(
            results=out.results,
            invocations=out.invocations,
            cost_usd_estimate=out.cost_usd_estimate,
            started=started,
        )
        return out

    if returncode != 0:
        detail = stderr.strip()[:500] or stdout.strip()[:500] or "unknown error"
        logger.warning("Codex invocation failed with rc=%d: %s", returncode, detail)
        out = CodexScoreBatch(
            results=_error_results(
                patents,
                error=f"invocation_error: codex exec failed (rc={returncode}): {detail}",
                raw=stdout,
            ),
            invocations=1,
            cost_usd_estimate=0.0,
        )
        _emit_score_done(
            results=out.results,
            invocations=out.invocations,
            cost_usd_estimate=out.cost_usd_estimate,
            started=started,
        )
        return out

    by_id = {p.patent_id: p for p in patents}
    results: List[ScoreResult] = []
    try:
        items = extract_json_array(stdout)
    except ValueError as exc:
        logger.warning("Codex returned non-JSON output: %s", exc)
        out = CodexScoreBatch(
            results=_error_results(patents, error=f"json_parse_error: {exc}", raw=stdout),
            invocations=1,
            cost_usd_estimate=CODEX_COST_USD_PER_BATCH_ESTIMATE,
        )
        _emit_score_done(
            results=out.results,
            invocations=out.invocations,
            cost_usd_estimate=out.cost_usd_estimate,
            started=started,
        )
        return out

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

    out = CodexScoreBatch(
        results=results,
        invocations=1,
        cost_usd_estimate=CODEX_COST_USD_PER_BATCH_ESTIMATE,
    )
    _emit_score_done(
        results=out.results,
        invocations=out.invocations,
        cost_usd_estimate=out.cost_usd_estimate,
        started=started,
    )
    return out
