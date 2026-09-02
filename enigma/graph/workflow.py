"""Wires the node functions into a LangGraph StateGraph.

Routing logic:
    ingest -> analysis -> hypothesis -> reproduce -> patch -> test -> (route)
                                                        ^                |
                                                        |__ retry <=max__|
                                                                         |
                                                                    finalize

The conditional edge after `test` is what prevents the classic ReAct-loop
failure mode of looping forever on a failing test: retry_count is checked
against max_retries on every pass, and the graph is compiled with a
recursion_limit as a hard backstop even if the counter logic had a bug.
"""
from __future__ import annotations

from functools import partial
from typing import Callable, Optional

from langgraph.graph import END, StateGraph

from enigma.graph.nodes import (
    analysis_node,
    finalize_node,
    hypothesis_node,
    ingest_node,
    patch_node,
    reproduce_node,
    test_node,
)
from enigma.graph.state import IncidentState
from enigma.llm.base import BaseLLMProvider
from enigma.sandbox.base import BaseSandbox


def _route_after_test(state: IncidentState) -> str:
    if state.test_result and state.test_result.passed:
        return "finalize"
    if state.retry_count >= state.max_retries:
        return "finalize"
    return "patch"


def build_workflow(
    llm: BaseLLMProvider,
    sandbox: BaseSandbox,
    sandbox_timeout: int,
    test_command: list[str],
    get_fixture_patch: Optional[Callable] = None,
):
    graph = StateGraph(IncidentState)

    graph.add_node("ingest", ingest_node)
    graph.add_node("analysis", analysis_node)
    graph.add_node("hypothesis", partial(hypothesis_node, llm=llm))
    graph.add_node("reproduce", partial(reproduce_node, sandbox=sandbox, timeout=sandbox_timeout))
    graph.add_node("patch", partial(patch_node, llm=llm, get_fixture_patch=get_fixture_patch))
    graph.add_node(
        "test", partial(test_node, sandbox=sandbox, timeout=sandbox_timeout, test_command=test_command)
    )
    graph.add_node("finalize", finalize_node)

    graph.set_entry_point("ingest")
    graph.add_edge("ingest", "analysis")
    graph.add_edge("analysis", "hypothesis")
    graph.add_edge("hypothesis", "reproduce")
    graph.add_edge("reproduce", "patch")
    graph.add_edge("patch", "test")
    graph.add_conditional_edges("test", _route_after_test, {"patch": "patch", "finalize": "finalize"})
    graph.add_edge("finalize", END)

    # Hard backstop: even if retry_count bookkeeping had a bug, LangGraph
    # itself will refuse to exceed this many super-steps.
    return graph.compile()
