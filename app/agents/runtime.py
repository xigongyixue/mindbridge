from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.enums import IntentType, MessageRole, RiskLevel
from app.models.entities import ChatMessage, ChatSession, UserAccount
from app.schemas.dtos import AiMessage
from app.services.ai import AiClient, PromptTemplates, has_consult_signal, has_high_risk_signal
from app.services.assessment import PsychologicalAssessmentService, PsychologyAssessment
from app.services.knowledge import KnowledgeService, SearchResult
from app.services.memory import RedisShortTermMemoryStore, compact_history_for_prompt
from app.services.skills import MindBridgeSkillLibrary


GENERAL_TASK_WORDS = [
    "java", "python", "javascript", "代码", "编程", "程序", "算法", "数据库", "spring", "maven",
    "前端", "后端", "项目", "接口", "bug", "报错", "作业", "论文", "翻译", "总结", "解释",
    "怎么写", "如何", "是什么", "为什么", "给我", "帮我", "推荐", "查询", "天气", "路线",
]


@dataclass
class AgentStep:
    step: int
    agent: str
    action: str
    observation: str


@dataclass
class AgentContext:
    user: UserAccount
    session: ChatSession
    original_input: str
    model_input: str
    memory_loaded: bool = False
    intent_routed: bool = False
    knowledge_handled: bool = False
    risk_assessed: bool = False
    response_planned: bool = False
    finished: bool = False
    memory_brief: str = "无相关历史记忆。"
    intent: IntentType | None = None
    risk_level: RiskLevel = RiskLevel.LOW
    assessment: PsychologyAssessment | None = None
    knowledge_query: str = ""
    retrieved_knowledge: list[SearchResult] = field(default_factory=list)
    model_history: list[AiMessage] = field(default_factory=list)
    response_messages: list[AiMessage] = field(default_factory=list)
    response_agent: str = ""
    response_plan: str = ""
    steps: list[AgentStep] = field(default_factory=list)


@dataclass
class AgentRunResult:
    intent: IntentType
    risk_level: RiskLevel
    assessment: PsychologyAssessment | None
    retrieved_knowledge: list[SearchResult]
    response_messages: list[AiMessage]
    steps: list[AgentStep]
    memory_brief: str
    collaboration_events: list[Any] = field(default_factory=list)
    collaboration_tasks: list[Any] = field(default_factory=list)
    collaboration_artifacts: list[Any] = field(default_factory=list)

    @property
    def requires_report(self) -> bool:
        return self.intent != IntentType.CHAT


class AgentRuntimeService:
    max_steps = 8

    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings
        self.ai = AiClient(settings)
        self.knowledge = KnowledgeService(db, settings)
        self.memory = RedisShortTermMemoryStore(settings)
        self.assessment = PsychologicalAssessmentService(self.ai)

    def run(self, user: UserAccount, session: ChatSession, original_input: str, model_input: str) -> AgentRunResult:
        context = AgentContext(user=user, session=session, original_input=original_input, model_input=model_input)
        agents = [
            self.memory_agent,
            self.supervisor_agent,
            self.knowledge_agent,
            self.risk_guardian_agent,
            self.companion_agent,
            self.counselor_agent,
        ]
        for step in range(1, self.max_steps + 1):
            if context.finished:
                break
            for agent in agents:
                if agent(step, context):
                    break
        return AgentRunResult(
            intent=context.intent or IntentType.CHAT,
            risk_level=context.risk_level,
            assessment=context.assessment,
            retrieved_knowledge=context.retrieved_knowledge,
            response_messages=context.response_messages,
            steps=context.steps,
            memory_brief=context.memory_brief,
        )

    def memory_agent(self, step: int, context: AgentContext) -> bool:
        if context.memory_loaded:
            return False
        history = self.memory.load_recent(context.session.public_id)
        source = "redis"
        if not history:
            rows = (
                self.db.query(ChatMessage)
                .filter(ChatMessage.session_id == context.session.id)
                .order_by(ChatMessage.created_at.desc())
                .limit(self.settings.redis_memory_max_messages)
                .all()
            )
            rows.reverse()
            history = self.memory.messages_from_rows(rows)
            if history:
                self.memory.replace(context.session.public_id, history)
                source = "mysql_seeded"
        compacted_history, deterministic_brief = compact_history_for_prompt(history, self.settings, context.model_input)
        context.model_history = self._bounded_model_history(
            [*compacted_history, AiMessage(role="user", content=context.model_input)]
        )
        context.memory_brief = self._summarize_memory(history, context.model_input, deterministic_brief)
        context.memory_loaded = True
        context.steps.append(AgentStep(step, "MemoryAgent", "READ_MEMORY", f"loaded {len(history)} messages from {source}"))
        return True

    def supervisor_agent(self, step: int, context: AgentContext) -> bool:
        if not context.memory_loaded or context.intent_routed:
            return False
        context.intent = self._classify(context.model_input, context.model_history)
        context.intent_routed = True
        if context.intent == IntentType.CHAT:
            context.knowledge_handled = True
            context.risk_assessed = True
        context.steps.append(AgentStep(step, "SupervisorAgent", "ROUTE_INTENT", f"intent={context.intent.value}"))
        return True

    def knowledge_agent(self, step: int, context: AgentContext) -> bool:
        if not context.intent_routed or context.knowledge_handled or context.intent == IntentType.CHAT:
            return False
        query = self._rewrite_query(context)
        retrieved = self.knowledge.retrieve(query, self.settings.knowledge_top_k)
        context.knowledge_query = query
        context.retrieved_knowledge = retrieved
        context.knowledge_handled = True
        context.steps.append(AgentStep(step, "KnowledgeAgent", "RETRIEVE_KNOWLEDGE", f"query={query}; retrieved={len(retrieved)}"))
        return True

    def risk_guardian_agent(self, step: int, context: AgentContext) -> bool:
        if not context.knowledge_handled or context.risk_assessed or context.intent == IntentType.CHAT:
            return False
        assessment = self.assessment.assess(context.model_input, context.model_history)
        if context.intent == IntentType.RISK and assessment.risk != RiskLevel.HIGH:
            assessment.risk = RiskLevel.HIGH
            assessment.emotion_score = max(assessment.emotion_score, 4.0)
        context.assessment = assessment
        context.risk_level = assessment.risk
        context.risk_assessed = True
        context.steps.append(AgentStep(step, "RiskGuardianAgent", "ASSESS_RISK", f"risk={assessment.risk.value}, emotion={assessment.emotion.value}"))
        return True

    def companion_agent(self, step: int, context: AgentContext) -> bool:
        if not context.intent_routed or context.intent != IntentType.CHAT or context.response_planned:
            return False
        context.risk_level = RiskLevel.LOW
        context.response_agent = "CompanionAgent"
        context.response_plan = "围绕用户当前问题直接、自然地回答。"
        context.response_messages = [
            PromptTemplates.answer_system_prompt(IntentType.CHAT, RiskLevel.LOW, "", context.user.display_name),
            AiMessage(role="system", content=f"当前由 CompanionAgent 负责回复。\n记忆摘要：\n{context.memory_brief}\n回复策略：\n{context.response_plan}"),
            *context.model_history,
        ]
        context.response_planned = True
        context.finished = True
        context.steps.append(AgentStep(step, "CompanionAgent", "PLAN_RESPONSE", "normal companion response planned"))
        return True

    def counselor_agent(self, step: int, context: AgentContext) -> bool:
        if not context.risk_assessed or context.intent == IntentType.CHAT or context.response_planned:
            return False
        context.response_agent = "CounselorAgent"
        context.response_plan = "先共情，再给出具体支持步骤；高风险时优先安全。"
        knowledge_context = "\n\n".join(f"- [{item.source}] {item.content}" for item in context.retrieved_knowledge)
        skill_context = MindBridgeSkillLibrary.response_skill_context(
            context.intent or IntentType.CONSULT,
            context.risk_level,
            context.original_input,
        )
        context.response_messages = [
            PromptTemplates.answer_system_prompt(
                context.intent or IntentType.CONSULT,
                context.risk_level,
                knowledge_context,
                context.user.display_name,
                skill_context,
            ),
            AiMessage(role="system", content=(
                f"当前由 CounselorAgent 负责回复。\n记忆摘要：\n{context.memory_brief}\n"
                f"KnowledgeAgent 检索 query：\n{context.knowledge_query}\n回复策略：\n{context.response_plan}"
            )),
            *context.model_history,
        ]
        context.response_planned = True
        context.finished = True
        context.steps.append(AgentStep(step, "CounselorAgent", "PLAN_RESPONSE", f"support response planned with risk={context.risk_level.value}"))
        return True

    def _classify(self, text: str, history: list[AiMessage]) -> IntentType:
        lowered = text.lower()
        if has_high_risk_signal(lowered):
            return IntentType.RISK
        if not has_consult_signal(lowered) and any(word in lowered for word in GENERAL_TASK_WORDS):
            return IntentType.CHAT
        try:
            label = self.ai.complete(PromptTemplates.intent_prompt(history, text)).upper()
            if "RISK" in label:
                return IntentType.RISK
            if "CONSULT" in label:
                return IntentType.CONSULT
            if "CHAT" in label:
                return IntentType.CHAT
        except Exception:
            pass
        return IntentType.CONSULT if has_consult_signal(lowered) else IntentType.CHAT

    def _rewrite_query(self, context: AgentContext) -> str:
        try:
            query = self.ai.complete([
                AiMessage(role="system", content="你是 MindBridge 的 KnowledgeAgent。把学生输入改写成适合检索校园心理知识库的中文查询词，只输出查询词。"),
                AiMessage(role="user", content=f"记忆摘要：\n{context.memory_brief}\n\n当前输入：\n{context.model_input}"),
            ]).strip()
            return (query or context.model_input)[:60]
        except Exception:
            return context.model_input

    def _bounded_model_history(self, history: list[AiMessage]) -> list[AiMessage]:
        limit = max(2, self.settings.chat_history_limit * 2)
        if len(history) <= limit:
            return history
        if history[0].role == "system":
            return [history[0], *history[-(limit - 1):]]
        return history[-limit:]

    def _summarize_memory(self, history: list[AiMessage], current_input: str, fallback: str) -> str:
        max_chars = max(120, self.settings.memory_summary_max_chars)
        if not history:
            return "无相关历史记忆。"
        try:
            summary = self.ai.complete([
                AiMessage(role="system", content="你是 MindBridge 的 MemoryAgent。只输出 1-3 条中文记忆要点，不输出风险等级或诊断。"),
                AiMessage(role="user", content=f"当前输入：\n{current_input}\n\n最近历史：\n{history[-12:]}"),
            ]).strip()
            return summary[:max_chars] or fallback
        except Exception:
            return fallback or "无相关历史记忆。"
