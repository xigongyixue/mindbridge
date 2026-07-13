from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.enums import IntentType, RiskLevel
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
    """记录 Agent 执行链中单步操作的追踪信息。"""
    step: int
    agent: str
    action: str
    observation: str


@dataclass
class AgentRunResult:
    """Agent 运行结束后返回给 harness 的统一结果契约。"""

    intent: IntentType
    risk_level: RiskLevel
    assessment: PsychologyAssessment | None
    retrieved_knowledge: list[SearchResult]
    response_messages: list[AiMessage]
    steps: list[AgentStep]
    memory_brief: str

    @property
    def requires_report(self) -> bool:
        """判断本轮是否需要生成心理风险评估报告（非 CHAT 意图时为 True）。"""
        return self.intent != IntentType.CHAT


@dataclass
class AgentContext:
    """在 LangGraph 各节点间传递的可变状态容器。"""

    user: UserAccount
    session: ChatSession
    original_input: str
    model_input: str
    memory_brief: str = "无相关历史记忆。"
    intent: IntentType | None = None
    risk_level: RiskLevel = RiskLevel.LOW
    assessment: PsychologyAssessment | None = None
    knowledge_query: str = ""
    retrieved_knowledge: list[SearchResult] = field(default_factory=list)
    model_history: list[AiMessage] = field(default_factory=list)
    response_messages: list[AiMessage] = field(default_factory=list)
    steps: list[AgentStep] = field(default_factory=list)


class GraphState(TypedDict):
    """LangGraph 状态图的状态类型，包装 AgentContext。"""
    context: AgentContext


class AgentRuntimeService:
    """基于 LangGraph 的 MindBridge Agent 运行时。

    固定 DAG 流水线：
        memory -> supervisor -> [knowledge -> risk_guardian -> counselor | companion] -> END

    CHAT 意图：memory -> supervisor -> companion -> END
    CONSULT/RISK 意图：memory -> supervisor -> knowledge -> risk_guardian -> counselor -> END
    """

    max_steps = 8

    def __init__(self, db: Session, settings: Settings):
        """初始化运行时，创建 AI 客户端、知识服务、记忆存储并编译 LangGraph 图。"""
        self.db = db
        self.settings = settings
        self.ai = AiClient(settings)
        self.knowledge = KnowledgeService(db, settings)
        self.memory = RedisShortTermMemoryStore(settings)
        self.assessment = PsychologicalAssessmentService(self.ai)
        self.graph = self._build_graph()

    def run(self, user: UserAccount, session: ChatSession, original_input: str, model_input: str) -> AgentRunResult:
        """执行一轮完整的 Agent DAG 流水线，返回运行结果。"""
        context = AgentContext(user=user, session=session, original_input=original_input, model_input=model_input)
        self.graph.invoke({"context": context}, {"recursion_limit": self.max_steps * 2})
        return AgentRunResult(
            intent=context.intent or IntentType.CHAT,
            risk_level=context.risk_level,
            assessment=context.assessment,
            retrieved_knowledge=context.retrieved_knowledge,
            response_messages=context.response_messages,
            steps=context.steps,
            memory_brief=context.memory_brief,
        )

    # --- 图构建 ---

    def _build_graph(self):
        """构建 LangGraph StateGraph，定义 6 个节点和条件边路由。"""
        from langgraph.graph import END, StateGraph

        graph = StateGraph(GraphState)
        graph.add_node("memory", self._memory_node)
        graph.add_node("supervisor", self._supervisor_node)
        graph.add_node("knowledge", self._knowledge_node)
        graph.add_node("risk_guardian", self._risk_guardian_node)
        graph.add_node("companion", self._companion_node)
        graph.add_node("counselor", self._counselor_node)

        graph.set_entry_point("memory")
        graph.add_edge("memory", "supervisor")
        graph.add_conditional_edges(
            "supervisor",
            self._route_after_supervisor,
            {"companion": "companion", "knowledge": "knowledge"},
        )
        graph.add_edge("knowledge", "risk_guardian")
        graph.add_edge("risk_guardian", "counselor")
        graph.add_edge("companion", END)
        graph.add_edge("counselor", END)
        return graph.compile()

    def _route_after_supervisor(self, state: GraphState) -> str:
        """根据意图分类结果决定 supervisor 之后的路由：CHAT 走 companion，其余走 knowledge。"""
        context = state["context"]
        if context.intent == IntentType.CHAT:
            return "companion"
        return "knowledge"

    # --- 图节点 ---

    def _memory_node(self, state: GraphState) -> GraphState:
        """加载短期记忆（Redis 优先，回退 MySQL），压缩历史并生成记忆摘要。"""
        context = state["context"]
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
        context.steps.append(AgentStep(len(context.steps) + 1, "MemoryAgent", "READ_MEMORY", f"loaded {len(history)} messages from {source}"))
        return state

    def _supervisor_node(self, state: GraphState) -> GraphState:
        """对用户输入进行意图分类（CHAT/CONSULT/RISK），决定后续流水线路径。"""
        context = state["context"]
        context.intent = self._classify(context.model_input, context.model_history)
        context.steps.append(AgentStep(len(context.steps) + 1, "SupervisorAgent", "ROUTE_INTENT", f"intent={context.intent.value}"))
        return state

    def _knowledge_node(self, state: GraphState) -> GraphState:
        """改写检索查询词并从知识库执行混合 RAG 检索。"""
        context = state["context"]
        query = self._rewrite_query(context)
        retrieved = self.knowledge.retrieve(query, self.settings.knowledge_top_k)
        context.knowledge_query = query
        context.retrieved_knowledge = retrieved
        context.steps.append(AgentStep(len(context.steps) + 1, "KnowledgeAgent", "RETRIEVE_KNOWLEDGE", f"query={query}; retrieved={len(retrieved)}"))
        return state

    def _risk_guardian_node(self, state: GraphState) -> GraphState:
        """执行心理风险评估，RISK 意图时强制提升至 HIGH 风险等级。"""
        context = state["context"]
        assessment = self.assessment.assess(context.model_input, context.model_history)
        if context.intent == IntentType.RISK and assessment.risk != RiskLevel.HIGH:
            assessment.risk = RiskLevel.HIGH
            assessment.emotion_score = max(assessment.emotion_score, 4.0)
        context.assessment = assessment
        context.risk_level = assessment.risk
        context.steps.append(AgentStep(len(context.steps) + 1, "RiskGuardianAgent", "ASSESS_RISK", f"risk={assessment.risk.value}, emotion={assessment.emotion.value}"))
        return state

    def _companion_node(self, state: GraphState) -> GraphState:
        """为 CHAT 意图构建日常陪伴回复的消息列表。"""
        context = state["context"]
        context.risk_level = RiskLevel.LOW
        context.response_messages = [
            PromptTemplates.answer_system_prompt(IntentType.CHAT, RiskLevel.LOW, "", context.user.display_name),
            AiMessage(
                role="system",
                content=(
                    f"当前由 CompanionAgent 负责回复。\n"
                    f"记忆摘要：\n{context.memory_brief}\n"
                    f"回复策略：\n围绕用户当前问题直接、自然地回答。"
                ),
            ),
            *context.model_history,
        ]
        context.steps.append(AgentStep(len(context.steps) + 1, "CompanionAgent", "PLAN_RESPONSE", "normal companion response planned"))
        return state

    def _counselor_node(self, state: GraphState) -> GraphState:
        """为 CONSULT/RISK 意图构建心理支持回复的消息列表，融合知识和 skill 上下文。"""
        context = state["context"]
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
            AiMessage(
                role="system",
                content=(
                    f"当前由 CounselorAgent 负责回复。\n"
                    f"记忆摘要：\n{context.memory_brief}\n"
                    f"KnowledgeAgent 检索 query：\n{context.knowledge_query}\n"
                    f"回复策略：\n先共情，再给出具体支持步骤；高风险时优先安全。"
                ),
            ),
            *context.model_history,
        ]
        context.steps.append(AgentStep(len(context.steps) + 1, "CounselorAgent", "PLAN_RESPONSE", f"support response planned with risk={context.risk_level.value}"))
        return state

    # --- 共享辅助函数（唯一副本） ---

    def _classify(self, text: str, history: list[AiMessage]) -> IntentType:
        """通过关键词硬规则和 LLM 调用对用户输入进行意图分类。"""
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
        """通过 LLM 将用户输入改写为适合知识库检索的中文查询词。"""
        try:
            query = self.ai.complete([
                AiMessage(role="system", content="你是 MindBridge 的 KnowledgeAgent。把学生输入改写成适合检索校园心理知识库的中文查询词，只输出查询词。"),
                AiMessage(role="user", content=f"记忆摘要：\n{context.memory_brief}\n\n当前输入：\n{context.model_input}"),
            ]).strip()
            return (query or context.model_input)[:60]
        except Exception:
            return context.model_input

    def _bounded_model_history(self, history: list[AiMessage]) -> list[AiMessage]:
        """将历史消息截断到配置的条数上限，保留首条 system 消息和最近若干轮。"""
        limit = max(2, self.settings.chat_history_limit * 2)
        if len(history) <= limit:
            return history
        if history[0].role == "system":
            return [history[0], *history[-(limit - 1):]]
        return history[-limit:]

    def _summarize_memory(self, history: list[AiMessage], current_input: str, fallback: str) -> str:
        """通过 LLM 将历史对话压缩为 1-3 条中文记忆要点，失败时回退到确定性摘要。"""
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
