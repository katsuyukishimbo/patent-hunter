"""Claude Code CLI scorer for the historical "Sonnet" judge role.

The scorer calls the local ``claude`` binary as a subprocess instead of a
direct API client. Claude Code chooses the configured default model (Opus 4.7
by default for the user's Max subscription), while this module keeps the
existing runner contract and ``model="sonnet"`` score labels.

Important cost guard: subprocesses run with ``cwd="/tmp"`` so Claude Code does
not ingest project-level CLAUDE.md, .claude/rules, or memory files as context.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, List, Optional

from .json_extract import extract_json_array, score_result_kwargs
from .prompts import SYSTEM_PROMPT, build_user_payload
from ..models import Patent, ScoreResult
from ..observability import emit

logger = logging.getLogger(__name__)

CLAUDE_SUBPROCESS_CWD = "/tmp"
CLAUDE_TIMEOUT_SECONDS = 180.0
MAX_SCORE_ATTEMPTS = 3
SCORE_RETRY_BASE_DELAY_SECONDS = 1.0
SCORE_RETRY_FACTOR = 2.0

RunnerOutput = str | tuple[str, str, int]
Runner = Callable[[List[str], float], Awaitable[RunnerOutput]]


@dataclass
class SonnetScoreBatch:
    results: List[ScoreResult]
    input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass
class _ClaudeCliResult:
    result_text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    is_error: bool


def _build_command(prompt: str, *, claude_bin: str) -> List[str]:
    return [claude_bin, "-p", prompt, "--output-format=json"]


def _result_from_json(obj: dict, patent_id: str, raw_text: str) -> ScoreResult:
    """Build a ScoreResult from one decoded JSON object."""
    return ScoreResult(model="sonnet", **score_result_kwargs(obj, patent_id, raw_text))


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _error_batch(
    patents: List[Patent],
    *,
    error: str,
    raw: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0.0,
) -> SonnetScoreBatch:
    return SonnetScoreBatch(
        results=[
            ScoreResult(patent_id=p.patent_id, model="sonnet", raw=raw, error=error)
            for p in patents
        ],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
    )


def _decode_cli_response(stdout: str) -> _ClaudeCliResult:
    try:
        obj = json.loads(stdout.strip())
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Claude CLI JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError(f"Claude CLI JSON was {type(obj).__name__}, expected object")

    usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
    result = obj.get("result")
    return _ClaudeCliResult(
        result_text="" if result is None else str(result),
        input_tokens=_safe_int(usage.get("input_tokens")),
        output_tokens=_safe_int(usage.get("output_tokens")),
        cost_usd=_safe_float(obj.get("total_cost_usd")),
        is_error=bool(obj.get("is_error")),
    )


async def _read_or_empty(stream: Any) -> bytes:
    if stream is None:
        return b""
    return await stream.read()


async def _run_claude(
    argv: List[str], timeout: float, *, cwd: str = CLAUDE_SUBPROCESS_CWD
) -> tuple[str, str, int]:
    """Run Claude Code and return stdout, stderr, and return code."""
    logger.debug("Claude argv: %s", " ".join(shlex.quote(a) for a in argv))
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b, waited_rc = await asyncio.wait_for(
            asyncio.gather(
                _read_or_empty(proc.stdout),
                _read_or_empty(proc.stderr),
                proc.wait(),
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise

    returncode = proc.returncode
    if returncode is None:
        returncode = waited_rc if isinstance(waited_rc, int) else 0
    return (
        (stdout_b or b"").decode("utf-8", "replace"),
        (stderr_b or b"").decode("utf-8", "replace"),
        int(returncode),
    )


async def _invoke_runner(
    runner: Runner | None,
    argv: List[str],
    timeout: float,
) -> tuple[str, str, int]:
    if runner is None:
        return await _run_claude(argv, timeout)

    out = await runner(argv, timeout)
    if isinstance(out, tuple):
        stdout, stderr, returncode = out
        return stdout, stderr, returncode
    return out, "", 0


def _retry_delay(attempt: int) -> float:
    return SCORE_RETRY_BASE_DELAY_SECONDS * (SCORE_RETRY_FACTOR ** (attempt - 1))


async def _invoke_with_retries(
    runner: Runner | None,
    argv: List[str],
    timeout: float,
) -> tuple[str, str, int]:
    last_error: BaseException | None = None
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
            model="sonnet",
            attempt=attempt + 1,
            reason=reason,
        )
        logger.warning(
            "Claude CLI transient failure (%s), retrying attempt %d/%d in %.1fs",
            reason,
            attempt + 1,
            MAX_SCORE_ATTEMPTS,
            delay,
        )
        await asyncio.sleep(delay)

    raise RuntimeError("unreachable retry state")


def _emit_score_done(
    *,
    results: List[ScoreResult],
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    started: float,
) -> None:
    emit(
        "score_done",
        model="sonnet",
        results=len(results),
        errors=sum(1 for r in results if r.error),
        duration_ms=int((time.perf_counter() - started) * 1000),
        cost_usd=round(cost_usd, 4),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


async def score_batch(
    patents: List[Patent],
    *,
    client: Runner | None = None,
    model: Optional[str] = None,
    runner: Runner | None = None,
    claude_bin: Optional[str] = None,
    timeout: float = CLAUDE_TIMEOUT_SECONDS,
) -> SonnetScoreBatch:
    """Score one batch with the local Claude Code CLI.

    ``client`` is retained as a backwards-compatible injection slot used by
    runner/graph fixtures; it now means an async subprocess-style runner rather
    than a direct API client. ``model`` is accepted for the historical signature but
    ignored because Claude Code owns model selection.
    """
    started = time.perf_counter()
    emit("score_started", model="sonnet", batch_size=len(patents))
    if not patents:
        _emit_score_done(
            results=[],
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            started=started,
        )
        return SonnetScoreBatch(results=[], input_tokens=0, output_tokens=0, cost_usd=0.0)

    if model:
        logger.debug("Ignoring model=%s; Claude Code CLI controls model selection", model)

    effective_runner = runner or client
    claude_bin = claude_bin or os.environ.get("CLAUDE_BIN", "claude")
    user_text = build_user_payload(patents)
    full_prompt = f"{SYSTEM_PROMPT}\n\n---\n\n{user_text}"
    argv = _build_command(full_prompt, claude_bin=claude_bin)

    logger.info("Claude CLI: scoring batch of %d patents", len(patents))
    try:
        stdout, stderr, returncode = await _invoke_with_retries(
            effective_runner, argv, timeout
        )
    except (asyncio.TimeoutError, RuntimeError, FileNotFoundError) as exc:
        logger.warning("Claude CLI invocation failed: %s", exc)
        out = _error_batch(patents, error=f"invocation_error: {exc}")
        _emit_score_done(
            results=out.results,
            input_tokens=out.input_tokens,
            output_tokens=out.output_tokens,
            cost_usd=out.cost_usd,
            started=started,
        )
        return out

    try:
        cli_result = _decode_cli_response(stdout)
    except ValueError as exc:
        message = f"json_parse_error: {exc}"
        if returncode != 0:
            stderr_tail = stderr.strip()[:500]
            message = (
                f"invocation_error: claude failed (rc={returncode}): "
                f"{stderr_tail or str(exc)}"
            )
        logger.warning("Claude CLI returned invalid wrapper JSON: %s", message)
        out = _error_batch(patents, error=message, raw=stdout)
        _emit_score_done(
            results=out.results,
            input_tokens=out.input_tokens,
            output_tokens=out.output_tokens,
            cost_usd=out.cost_usd,
            started=started,
        )
        return out

    if returncode != 0 or cli_result.is_error:
        detail = cli_result.result_text.strip() or stderr.strip() or "unknown error"
        prefix = (
            f"invocation_error: claude failed (rc={returncode})"
            if returncode != 0
            else "claude_cli_error"
        )
        logger.warning("Claude CLI reported failure: %s: %s", prefix, detail)
        out = _error_batch(
            patents,
            error=f"{prefix}: {detail}",
            raw=cli_result.result_text,
            input_tokens=cli_result.input_tokens,
            output_tokens=cli_result.output_tokens,
            cost_usd=cli_result.cost_usd,
        )
        _emit_score_done(
            results=out.results,
            input_tokens=out.input_tokens,
            output_tokens=out.output_tokens,
            cost_usd=out.cost_usd,
            started=started,
        )
        return out

    by_id = {p.patent_id: p for p in patents}
    results: List[ScoreResult] = []
    try:
        items = extract_json_array(cli_result.result_text)
    except ValueError as exc:
        logger.warning("Claude CLI result field was non-JSON: %s", exc)
        out = _error_batch(
            patents,
            error=f"json_parse_error: {exc}",
            raw=cli_result.result_text,
            input_tokens=cli_result.input_tokens,
            output_tokens=cli_result.output_tokens,
            cost_usd=cli_result.cost_usd,
        )
        _emit_score_done(
            results=out.results,
            input_tokens=out.input_tokens,
            output_tokens=out.output_tokens,
            cost_usd=out.cost_usd,
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
        results.append(_result_from_json(obj, pid, cli_result.result_text))
        seen.add(pid)

    for p in patents:
        if p.patent_id not in seen:
            results.append(
                ScoreResult(
                    patent_id=p.patent_id,
                    model="sonnet",
                    raw=cli_result.result_text,
                    error="missing_from_batch_response",
                )
            )

    out = SonnetScoreBatch(
        results=results,
        input_tokens=cli_result.input_tokens,
        output_tokens=cli_result.output_tokens,
        cost_usd=cli_result.cost_usd,
    )
    _emit_score_done(
        results=out.results,
        input_tokens=out.input_tokens,
        output_tokens=out.output_tokens,
        cost_usd=out.cost_usd,
        started=started,
    )
    return out
