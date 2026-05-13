"""Jinja2-based HTML report renderer."""

from __future__ import annotations

from importlib.resources import files
from typing import List

from jinja2 import DictLoader, Environment, select_autoescape

from ..models import RunStats, ScoredPatent

TEMPLATE_NAME = "report.html.j2"


def _env() -> Environment:
    template_text = (
        files("patent_hunter.io")
        .joinpath("templates", TEMPLATE_NAME)
        .read_text(encoding="utf-8")
    )
    return Environment(
        loader=DictLoader({TEMPLATE_NAME: template_text}),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_report(
    *,
    week_label: str,
    top: List[ScoredPatent],
    stats: RunStats,
    score_threshold: int,
) -> str:
    env = _env()
    template = env.get_template("report.html.j2")
    return template.render(
        week_label=week_label,
        top=top,
        stats=stats,
        score_threshold=score_threshold,
    )
