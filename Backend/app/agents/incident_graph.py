"""Incident lifecycle as a LangGraph state graph.

    START → investigate ─┬─(closed)──────────────────────────────► END
                         └─► decide ─┬─(act)───► execute ─┬─► verify ─► END
                                     ├─(human)─► human_gate ──────────► END   (resumed by /approve)
                                     └─(done)────────────────────────► END

Every node is a thin wrapper over a deterministic PayFlow stage, so the graph
is the orchestration layer only: no LLM inside it can move money. If LangGraph
is not installed the identical node functions run in a sequential fallback.
"""

import logging
import uuid
from typing import Any, TypedDict

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import SessionLocal

logger = logging.getLogger(__name__)

try:  # optional dependency
    from langchain_core.runnables import RunnableConfig
    from langgraph.graph import END, START, StateGraph

    LANGGRAPH_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without langgraph
    RunnableConfig = dict  # type: ignore[misc,assignment]
    LANGGRAPH_AVAILABLE = False


class IncidentState(TypedDict, total=False):
    incident_id: str
    force_fallback: bool
    delay: float
    route: str  # investigate → decide → act|human|done → verify|done
    action_id: str | None
    status: str


Factory = async_sessionmaker[AsyncSession]


def _factory(config: RunnableConfig | None) -> Factory:
    return ((config or {}).get("configurable") or {}).get("factory") or SessionLocal


async def node_investigate(state: IncidentState, config: RunnableConfig | None = None) -> dict:
    from app.services.orchestrator import investigate_step

    async with _factory(config)() as session:
        alive = await investigate_step(session, uuid.UUID(state["incident_id"]),
                                       force_fallback=state.get("force_fallback", False), delay=state.get("delay", 0.0))
    return {"route": "decide" if alive else "done"}


async def node_decide(state: IncidentState, config: RunnableConfig | None = None) -> dict:
    from app.services.orchestrator import decide_step

    async with _factory(config)() as session:
        decision = await decide_step(session, uuid.UUID(state["incident_id"]), delay=state.get("delay", 0.0))
    return {"route": decision.route, "action_id": str(decision.action_id) if decision.action_id else None}


async def node_execute(state: IncidentState, config: RunnableConfig | None = None) -> dict:
    from app.services.orchestrator import _pause, execute_step

    await _pause(state.get("delay", 0.0))
    async with _factory(config)() as session:
        route, _ = await execute_step(session, uuid.UUID(state["action_id"]), delay=state.get("delay", 0.0))
    return {"route": route}


async def node_verify(state: IncidentState, config: RunnableConfig | None = None) -> dict:
    from app.services.orchestrator import verify_step

    async with _factory(config)() as session:
        result = await verify_step(session, uuid.UUID(state["action_id"]), delay=state.get("delay", 0.0))
    return {"route": "done", "status": result.incident_status}


async def node_human_gate(state: IncidentState, config: RunnableConfig | None = None) -> dict:
    # The graph pauses here; POST /api/actions/{id}/approve resumes at execute → verify.
    return {"route": "done"}


def _after(key: str):
    def router(state: IncidentState) -> str:
        return state.get("route", "done")
    router.__name__ = f"route_after_{key}"
    return router


def build_graph():
    if not LANGGRAPH_AVAILABLE:
        return None
    graph = StateGraph(IncidentState)
    graph.add_node("investigate", node_investigate)
    graph.add_node("decide", node_decide)
    graph.add_node("execute", node_execute)
    graph.add_node("verify", node_verify)
    graph.add_node("human_gate", node_human_gate)
    graph.add_edge(START, "investigate")
    graph.add_conditional_edges("investigate", _after("investigate"), {"decide": "decide", "done": END})
    graph.add_conditional_edges("decide", _after("decide"), {"act": "execute", "human": "human_gate", "done": END})
    graph.add_conditional_edges("execute", _after("execute"), {"verify": "verify", "done": END})
    graph.add_edge("verify", END)
    graph.add_edge("human_gate", END)
    return graph.compile()


_COMPILED: Any = None


def compiled():
    global _COMPILED
    if _COMPILED is None:
        _COMPILED = build_graph()
    return _COMPILED


async def _sequential(state: IncidentState, config: dict) -> IncidentState:
    state = {**state, **await node_investigate(state, config)}
    if state["route"] == "done":
        return state
    state = {**state, **await node_decide(state, config)}
    if state["route"] == "human":
        return {**state, **await node_human_gate(state, config)}
    if state["route"] != "act":
        return state
    state = {**state, **await node_execute(state, config)}
    if state["route"] == "verify":
        state = {**state, **await node_verify(state, config)}
    return state


async def run_incident_graph(incident_id: uuid.UUID, *, factory: Factory | None = None,
                             force_fallback: bool = False, delay: float = 0.0) -> str | None:
    from app.services.incident_service import IncidentService

    factory = factory or SessionLocal
    state: IncidentState = {"incident_id": str(incident_id), "force_fallback": force_fallback, "delay": delay}
    config = {"configurable": {"factory": factory}}
    graph = compiled()
    if graph is not None:
        await graph.ainvoke(state, config=config)
    else:
        await _sequential(state, config)
    async with factory() as session:
        return (await IncidentService(session).reload(incident_id)).status


def describe() -> dict:
    """Graph topology for the console (nodes, edges, mermaid)."""
    nodes = [
        {"id": "investigate", "label": "AI investigate", "kind": "ai"},
        {"id": "decide", "label": "Policy decide", "kind": "policy"},
        {"id": "human_gate", "label": "Human approval", "kind": "human"},
        {"id": "execute", "label": "Execute action", "kind": "action"},
        {"id": "verify", "label": "Verify + reconcile", "kind": "verification"},
    ]
    edges = [
        {"from": "START", "to": "investigate"}, {"from": "investigate", "to": "decide", "label": "open"},
        {"from": "investigate", "to": "END", "label": "closed"}, {"from": "decide", "to": "execute", "label": "ALLOW"},
        {"from": "decide", "to": "human_gate", "label": "HUMAN_APPROVAL_REQUIRED"},
        {"from": "decide", "to": "END", "label": "escalated"}, {"from": "execute", "to": "verify", "label": "ok"},
        {"from": "execute", "to": "END", "label": "failed"}, {"from": "verify", "to": "END"},
        {"from": "human_gate", "to": "execute", "label": "approved (resume)"},
    ]
    mermaid = None
    graph = compiled()
    if graph is not None:
        try:
            mermaid = graph.get_graph().draw_mermaid()
        except Exception:  # noqa: BLE001
            mermaid = None
    return {"engine": "langgraph" if graph is not None else "sequential-fallback", "nodes": nodes, "edges": edges,
            "mermaid": mermaid}
