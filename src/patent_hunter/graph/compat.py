"""Small LangGraph compatibility layer for offline test environments.

The project depends on real ``langgraph`` in ``pyproject.toml``. This module is
only used when that dependency cannot be installed, which is common in the
sandbox because network access is disabled. It implements the tiny subset of
StateGraph used by P2 so tests still exercise the graph shape and parallel
fan-out/fan-in behavior.
"""

from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable

START = "__start__"
END = "__end__"


class MemorySaver:
    """In-memory checkpoint stand-in with deterministic thread_id storage."""

    def __init__(self) -> None:
        self.storage: dict[str, dict[str, Any]] = {}

    def save(self, thread_id: str, state: dict[str, Any]) -> None:
        self.storage[thread_id] = dict(state)


class StateGraph:
    """Subset of ``langgraph.graph.StateGraph`` used by this repository."""

    def __init__(self, state_schema: type) -> None:
        self.state_schema = state_schema
        self.nodes: dict[str, Callable[..., Any]] = {}
        self.edges: dict[str, list[str]] = defaultdict(list)

    def add_node(self, name: str, fn: Callable[..., Any]) -> None:
        self.nodes[name] = fn

    def add_edge(self, source: str | list[str], target: str) -> None:
        sources = source if isinstance(source, list) else [source]
        for one_source in sources:
            self.edges[one_source].append(target)

    def compile(self, *, checkpointer: MemorySaver | None = None):
        return _CompiledStateGraph(self, checkpointer)


@dataclass
class _DrawableGraph:
    edges: dict[str, list[str]]

    def draw_mermaid(self) -> str:
        lines = ["graph TD;"]
        for source, targets in self.edges.items():
            left = _mermaid_node(source)
            for target in targets:
                lines.append(f"    {left} --> {_mermaid_node(target)};")
        return "\n".join(lines)


class _CompiledStateGraph:
    def __init__(self, graph: StateGraph, checkpointer: MemorySaver | None) -> None:
        self._graph = graph
        self._checkpointer = checkpointer

    def get_graph(self) -> _DrawableGraph:
        return _DrawableGraph(self._graph.edges)

    def invoke(self, state: dict[str, Any], config: dict[str, Any] | None = None):
        return asyncio.run(self.ainvoke(state, config=config))

    async def ainvoke(
        self, state: dict[str, Any], config: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        merged: dict[str, Any] = dict(state)
        completed = {START}
        executed: set[str] = set()
        predecessors = _predecessors(self._graph.edges)

        while True:
            ready = [
                name
                for name in self._graph.nodes
                if name not in executed
                and all(pred in completed for pred in predecessors.get(name, []))
            ]
            if not ready:
                break

            results = await asyncio.gather(
                *[self._run_node(name, merged) for name in ready]
            )
            for name, result in zip(ready, results):
                executed.add(name)
                completed.add(name)
                if result:
                    merged.update(result)

        thread_id = _thread_id_from_config(config)
        if self._checkpointer is not None and thread_id is not None:
            self._checkpointer.save(thread_id, merged)
        return merged

    async def _run_node(self, name: str, state: dict[str, Any]) -> dict[str, Any]:
        out = self._graph.nodes[name](state)
        if inspect.isawaitable(out):
            out = await out
        return dict(out or {})


def _predecessors(edges: dict[str, list[str]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for source, targets in edges.items():
        for target in targets:
            if target != END:
                out[target].append(source)
    return out


def _thread_id_from_config(config: dict[str, Any] | None) -> str | None:
    if not config:
        return None
    configurable = config.get("configurable") or {}
    thread_id = configurable.get("thread_id")
    return str(thread_id) if thread_id else None


def _mermaid_node(name: str) -> str:
    if name == START:
        return "__start__([START])"
    if name == END:
        return "__end__([END])"
    return name
