from __future__ import annotations

from importlib.util import find_spec

from sqlalchemy.orm import Session

from app.agents.runtime import AgentRuntimeService
from app.core.config import Settings


def create_agent_runtime(db: Session, settings: Settings) -> AgentRuntimeService:
    if wants_event_driven(settings):
        from app.agents.event_driven_runtime import EventDrivenAgentRuntimeService

        return EventDrivenAgentRuntimeService(db, settings)
    if wants_langgraph(settings) and langgraph_available():
        from app.agents.langgraph_runtime import LangGraphAgentRuntimeService

        return LangGraphAgentRuntimeService(db, settings)
    return AgentRuntimeService(db, settings)


def agent_framework_status(settings: Settings) -> dict:
    requested = settings.agent_framework.lower()
    available = langgraph_available()
    if requested in {"event_driven_multi_agent", "multi_agent", "actors"}:
        active = "event_driven_multi_agent"
    elif requested == "langgraph" and available:
        active = "langgraph"
    else:
        active = "custom"
    return {
        "requested": requested,
        "active": active,
        "langgraphAvailable": available,
        "fallback": active != requested and not (requested == "multi_agent" and active == "event_driven_multi_agent"),
    }


def wants_event_driven(settings: Settings) -> bool:
    return settings.agent_framework.lower() in {"event_driven_multi_agent", "multi_agent", "actors"}


def wants_langgraph(settings: Settings) -> bool:
    return settings.agent_framework.lower() == "langgraph"


def langgraph_available() -> bool:
    return find_spec("langgraph") is not None
