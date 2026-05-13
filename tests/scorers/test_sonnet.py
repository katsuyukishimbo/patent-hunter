"""Scorer tests. Both Anthropic SDK and codex subprocess are stubbed."""

from __future__ import annotations

import json
from types import SimpleNamespace

from patent_hunter.scorers import sonnet as scorer_sonnet

from tests.conftest import make_patent


def _patent(pid: str = "8234811", title: str = "Self-watering planter insert"):
    return make_patent(pid=pid, title=title, cpc_code="A47G")


# ---- Sonnet ---------------------------------------------------------------


class _FakeMessages:
    def __init__(self, payload: str, input_tokens: int = 500, output_tokens: int = 200):
        self.payload = payload
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(
            content=[SimpleNamespace(text=self.payload)],
            usage=SimpleNamespace(
                input_tokens=self.input_tokens, output_tokens=self.output_tokens
            ),
        )


class _FakeAnthropic:
    def __init__(self, payload: str, **kw):
        self.messages = _FakeMessages(payload, **kw)


def test_sonnet_parses_clean_json():
    payload = json.dumps(
        [
            {
                "patent_id": "8234811",
                "plain_english": "Self-watering planter.",
                "consumer_viable": True,
                "bom_estimate": "$1.60-2.10",
                "amazon_gap": True,
                "review_signal": "wicks clog quickly",
                "score": 8,
            }
        ]
    )
    client = _FakeAnthropic(payload)
    out = scorer_sonnet.score_batch_sync([_patent()], client=client)
    assert out.results[0].score == 8
    assert out.results[0].consumer_viable is True
    assert out.results[0].error is None
    assert out.input_tokens == 500
    assert out.output_tokens == 200
    assert out.cost_usd > 0


def test_sonnet_marks_missing_patents_as_errored():
    # Model returned only 1 of the 2 patents.
    payload = json.dumps([{"patent_id": "A", "score": 9}])
    client = _FakeAnthropic(payload)
    a = _patent(pid="A")
    b = _patent(pid="B")
    out = scorer_sonnet.score_batch_sync([a, b], client=client)
    errs = {r.patent_id: r.error for r in out.results}
    assert errs["A"] is None
    assert errs["B"] is not None and "missing" in errs["B"]


def test_sonnet_handles_non_json_output_cleanly():
    client = _FakeAnthropic("I refuse, sorry.")
    out = scorer_sonnet.score_batch_sync([_patent()], client=client)
    assert out.results[0].error and "json_parse_error" in out.results[0].error


def test_sonnet_clamps_score_range():
    payload = json.dumps([{"patent_id": "8234811", "score": 99}])
    client = _FakeAnthropic(payload)
    out = scorer_sonnet.score_batch_sync([_patent()], client=client)
    assert out.results[0].score == 10
