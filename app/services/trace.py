from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from app.agents.runtime import AgentRunResult
from app.models.entities import AgentRunTrace, ChatSession, UserAccount


class AgentTraceService:
    """持久化 Agent 运行追踪记录的服务。"""

    def __init__(self, db: Session):
        """初始化追踪服务，绑定数据库会话。"""
        self.db = db

    def save_run(
        self,
        user: UserAccount,
        session: ChatSession,
        original_input: str,
        sanitized_input: str,
        memory_brief: str,
        agent_run: AgentRunResult,
        report_id: int | None,
    ) -> AgentRunTrace:
        """将一轮 Agent 运行的完整追踪数据（步骤、知识、消息、评估）保存到数据库。"""
        trace = AgentRunTrace(
            user_id=user.id,
            session_id=session.id,
            report_id=report_id,
            intent=agent_run.intent.value,
            risk_level=agent_run.risk_level.value,
            original_input=original_input,
            sanitized_input=sanitized_input,
            memory_brief=memory_brief,
            agent_steps_json=_json(agent_run.steps),
            retrieved_knowledge_json=_json(agent_run.retrieved_knowledge),
            response_messages_json=_json(agent_run.response_messages),
            assessment_json=_json(agent_run.assessment or {}),
        )
        self.db.add(trace)
        self.db.commit()
        self.db.refresh(trace)
        return trace


def _json(value: Any) -> str:
    """将任意 Python 对象序列化为 JSON 字符串，支持 dataclass/enum/pydantic 模型。"""
    return json.dumps(_to_jsonable(value), ensure_ascii=False, default=str)


def _to_jsonable(value: Any) -> Any:
    """递归地将 dataclass、enum、pydantic 模型等转换为可 JSON 序列化的原生类型。"""
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    if hasattr(value, "model_dump"):
        return _to_jsonable(value.model_dump())
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    return value
