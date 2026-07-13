from __future__ import annotations

from importlib.util import find_spec

from sqlalchemy.orm import Session

from app.agents.runtime import AgentRuntimeService
from app.core.config import Settings


def create_agent_runtime(db: Session, settings: Settings) -> AgentRuntimeService:
    """创建并返回 Agent 运行时实例（统一使用 LangGraph DAG 运行时）。"""
    return AgentRuntimeService(db, settings)


def agent_framework_status(settings: Settings) -> dict:
    """返回当前 Agent 框架的状态信息，包括请求的框架、实际激活的框架和可用性。"""
    available = langgraph_available()
    return {
        "requested": settings.agent_framework.lower(),
        "active": "langgraph",
        "langgraphAvailable": available,
        "fallback": False,
    }


def langgraph_available() -> bool:
    """检测当前环境中是否安装了 langgraph 包。"""
    return find_spec("langgraph") is not None
