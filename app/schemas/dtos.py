from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """对话请求模型。"""

    message: str = Field(min_length=1)
    sessionId: Optional[str] = None


class ChatStreamEvent(BaseModel):
    """对话流式事件模型。"""

    sessionId: Optional[str] = None
    content: Optional[str] = None
    message: Optional[str] = None
    type: str


class KnowledgeIngestRequest(BaseModel):
    """知识录入请求模型。"""

    source: str
    content: str


class KnowledgeIngestResponse(BaseModel):
    """知识录入响应模型。"""

    source: str
    chunks: int


class ReportResponse(BaseModel):
    """心理报告响应模型。"""

    id: int
    sessionId: str
    username: str
    displayName: str
    content: str
    intent: str
    emotion: str
    emotionScore: float
    riskLevel: str
    confidence: float
    summary: str
    createdAt: datetime


class ConversationMessageResponse(BaseModel):
    """对话消息响应模型。"""

    role: str
    content: str
    createdAt: datetime


class ConversationResponse(BaseModel):
    """完整对话响应模型。"""

    sessionId: str
    title: str
    messages: list[ConversationMessageResponse]


class ToolRecordResponse(BaseModel):
    """工具记录响应模型。"""

    id: int
    reportId: int
    status: str
    message: str
    createdAt: datetime
    channel: Optional[str] = None
    recipient: Optional[str] = None
    filePath: Optional[str] = None


class RiskCaseResponse(BaseModel):
    """风险个案响应模型。"""

    id: int
    reportId: int
    riskLevel: str
    status: str
    owner: str
    summary: str
    handoffSummary: str
    acknowledgedBy: Optional[str] = None
    acknowledgedAt: Optional[datetime] = None
    createdAt: datetime
    updatedAt: datetime


class CaseNoteResponse(BaseModel):
    """个案备注响应模型。"""

    id: int
    caseId: int
    actor: str
    note: str
    createdAt: datetime


class ToolJobResponse(BaseModel):
    """工具任务响应模型。"""

    id: int
    reportId: int
    kind: str
    status: str
    attempts: int
    maxAttempts: int
    dependsOnJobId: Optional[int] = None
    runAfter: datetime
    lastError: str
    createdAt: datetime
    updatedAt: datetime


class DeadLetterResponse(BaseModel):
    """死信记录响应模型。"""

    id: int
    jobId: Optional[int] = None
    reportId: int
    kind: str
    reason: str
    payload: str
    createdAt: datetime


class AgentRunTraceResponse(BaseModel):
    """智能体运行轨迹响应模型。"""

    id: int
    sessionId: str
    reportId: Optional[int] = None
    username: str
    intent: str
    riskLevel: str
    originalInput: str
    sanitizedInput: str
    memoryBrief: str
    agentSteps: list[dict[str, Any]]
    retrievedKnowledge: list[dict[str, Any]]
    responseMessages: list[dict[str, Any]]
    assessment: dict[str, Any]
    createdAt: datetime


class ToolAuditResponse(BaseModel):
    """工具审计响应模型。"""

    id: int
    jobId: Optional[int] = None
    reportId: Optional[int] = None
    toolName: str
    policy: str
    allowed: bool
    status: str
    reason: str
    payload: dict[str, Any]
    createdAt: datetime
    updatedAt: datetime


class AiMessage(BaseModel):
    """AI 消息模型。"""

    role: str
    content: str


def authority(role: str) -> dict[str, Any]:
    """将角色转换为权限字典。"""
    return {"authority": role}
