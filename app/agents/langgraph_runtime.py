from __future__ import annotations

from typing import TypedDict

from sqlalchemy.orm import Session

from app.agents.runtime import AgentContext, AgentRunResult, AgentRuntimeService
from app.core.config import Settings
from app.core.enums import IntentType
from app.models.entities import ChatSession, UserAccount


class GraphState(TypedDict):
    context: AgentContext


class LangGraphAgentRuntimeService(AgentRuntimeService):
    """LangGraph implementation of the bounded MindBridge agent loop."""

    framework_name = "langgraph"

    def __init__(self, db: Session, settings: Settings):
        super().__init__(db, settings)
        self.graph = self._build_graph()

    def run(self, user: UserAccount, session: ChatSession, original_input: str, model_input: str) -> AgentRunResult:
        context = AgentContext(user=user, session=session, original_input=original_input, model_input=model_input)
        graph_limit = self.max_steps * 3 + 2
        state = self.graph.invoke({"context": context}, {"recursion_limit": graph_limit})
        result_context = state["context"]
        return AgentRunResult(
            intent=result_context.intent or IntentType.CHAT,
            risk_level=result_context.risk_level,
            assessment=result_context.assessment,
            retrieved_knowledge=result_context.retrieved_knowledge,
            response_messages=result_context.response_messages,
            steps=result_context.steps,
            memory_brief=result_context.memory_brief,
        )

    def _build_graph(self):
        from langgraph.graph import END, StateGraph

        graph = StateGraph(GraphState)
        graph.add_node("controller", self._controller_node)
        graph.add_node("memory", self._memory_node)
        graph.add_node("supervisor", self._supervisor_node)
        graph.add_node("knowledge", self._knowledge_node)
        graph.add_node("risk_guardian", self._risk_guardian_node)
        graph.add_node("companion", self._companion_node)
        graph.add_node("counselor", self._counselor_node)

        graph.set_entry_point("controller")
        graph.add_conditional_edges(
            "controller",
            self._select_next_agent,
            {
                "memory": "memory",
                "supervisor": "supervisor",
                "knowledge": "knowledge",
                "risk_guardian": "risk_guardian",
                "companion": "companion",
                "counselor": "counselor",
                "end": END,
            },
        )
        graph.add_edge("memory", "controller")
        graph.add_edge("supervisor", "controller")
        graph.add_edge("knowledge", "controller")
        graph.add_edge("risk_guardian", "controller")
        graph.add_edge("companion", "controller")
        graph.add_edge("counselor", "controller")
        return graph.compile()

    def _controller_node(self, state: GraphState) -> GraphState:
        return state

    def _memory_node(self, state: GraphState) -> GraphState:
        self._run_agent(state, self.memory_agent)
        return state

    def _supervisor_node(self, state: GraphState) -> GraphState:
        self._run_agent(state, self.supervisor_agent)
        return state

    def _knowledge_node(self, state: GraphState) -> GraphState:
        self._run_agent(state, self.knowledge_agent)
        return state

    def _risk_guardian_node(self, state: GraphState) -> GraphState:
        self._run_agent(state, self.risk_guardian_agent)
        return state

    def _companion_node(self, state: GraphState) -> GraphState:
        self._run_agent(state, self.companion_agent)
        return state

    def _counselor_node(self, state: GraphState) -> GraphState:
        self._run_agent(state, self.counselor_agent)
        return state

    def _run_agent(self, state: GraphState, agent) -> None:
        context = state["context"]
        if context.finished or len(context.steps) >= self.max_steps:
            context.finished = True
            return
        before = len(context.steps)
        ran = agent(before + 1, context)
        if not ran and len(context.steps) == before:
            context.finished = True

    def _select_next_agent(self, state: GraphState) -> str:
        context = state["context"]
        if context.finished or len(context.steps) >= self.max_steps:
            return "end"
        if not context.memory_loaded:
            return "memory"
        if not context.intent_routed:
            return "supervisor"
        if context.intent == IntentType.CHAT:
            return "companion" if not context.response_planned else "end"
        if not context.knowledge_handled:
            return "knowledge"
        if not context.risk_assessed:
            return "risk_guardian"
        if not context.response_planned:
            return "counselor"
        return "end"
