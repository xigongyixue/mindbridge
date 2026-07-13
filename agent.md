# MindBridge Agent 架构说明

本文档描述 MindBridge 项目的 Agent 编排架构，包括 DAG 流水线、节点职责、状态管理与路由逻辑。

---

## 架构概览

MindBridge 使用 **LangGraph StateGraph** 构建单一 DAG 运行时，替代了早期的三套运行时（自定义循环、LangGraph 空壳、事件驱动 Actor 模型）。所有 Agent 逻辑统一在 `app/agents/runtime.py` 中实现。

### DAG 流水线

```
memory -> supervisor -> [knowledge -> risk_guardian -> counselor | companion] -> END
```

- **CHAT 意图**：`memory -> supervisor -> companion -> END`
- **CONSULT/RISK 意图**：`memory -> supervisor -> knowledge -> risk_guardian -> counselor -> END`

路由由 `supervisor` 节点后的条件边 `_route_after_supervisor` 决定。

---

## 核心文件

| 文件 | 职责 |
|------|------|
| `app/agents/runtime.py` | LangGraph DAG 运行时，定义全部节点、边与辅助函数 |
| `app/agents/factory.py` | 运行时工厂，直接返回 `AgentRuntimeService` 实例 |
| `app/agents/harness.py` | 单轮 Agent run 的运行时 harness，管理脱敏、持久化、报告、工具派发 |

---

## 状态管理

### AgentContext

`AgentContext` 是在 LangGraph 各节点间传递的**可变状态容器**。每个节点接收并修改同一个 `AgentContext` 实例。

| 字段 | 类型 | 说明 |
|------|------|------|
| `user` | `UserAccount` | 当前登录用户 |
| `session` | `ChatSession` | 当前聊天会话 |
| `original_input` | `str` | 用户原始输入（脱敏前） |
| `model_input` | `str` | 脱敏后的输入（送入 LLM） |
| `memory_brief` | `str` | 记忆摘要 |
| `intent` | `IntentType \| None` | 意图分类结果 |
| `risk_level` | `RiskLevel` | 风险等级（默认 LOW） |
| `assessment` | `PsychologyAssessment \| None` | 心理评估结果 |
| `knowledge_query` | `str` | 改写后的检索查询词 |
| `retrieved_knowledge` | `list[SearchResult]` | RAG 检索结果 |
| `model_history` | `list[AiMessage]` | 压缩后的历史消息 |
| `response_messages` | `list[AiMessage]` | 回复消息列表 |
| `steps` | `list[AgentStep]` | 执行步骤追踪 |

### GraphState

```python
class GraphState(TypedDict):
    context: AgentContext
```

LangGraph 的状态类型，包装 `AgentContext`。

---

## 节点详解

### 1. MemoryAgent（`_memory_node`）

**职责**：加载短期记忆，压缩历史，生成记忆摘要。

**流程**：
1. 从 Redis 加载最近消息（`RedisShortTermMemoryStore.load_recent`）。
2. Redis 为空时回退到 MySQL 查询，并回填 Redis。
3. 调用 `compact_history_for_prompt` 压缩历史为「system 摘要 + 最近 N 条」。
4. 调用 `_summarize_memory` 通过 LLM 生成 1-3 条中文记忆要点。

**输出**：`memory_brief`、`model_history`

---

### 2. SupervisorAgent（`_supervisor_node`）

**职责**：意图分类，决定后续流水线路径。

**分类逻辑**（`_classify`）：
1. 高风险词命中 -> `RISK`
2. 无咨询信号词且命中通用任务词 -> `CHAT`
3. 调用 LLM 做意图分类（RISK/CONSULT/CHAT）
4. LLM 失败时，有咨询信号 -> `CONSULT`，否则 -> `CHAT`

**输出**：`intent`

**路由**：`_route_after_supervisor` 根据 `intent` 选择下一节点。

---

### 3. KnowledgeAgent（`_knowledge_node`）

**职责**：改写检索查询，执行混合 RAG 检索。

**流程**：
1. 调用 `_rewrite_query` 通过 LLM 将用户输入改写为适合检索的中文查询词（截断 60 字符）。
2. 调用 `KnowledgeService.retrieve` 执行混合检索（向量召回 + BM25 + 本地 reranker）。

**输出**：`knowledge_query`、`retrieved_knowledge`

---

### 4. RiskGuardianAgent（`_risk_guardian_node`）

**职责**：执行心理风险评估。

**流程**：
1. 调用 `PsychologicalAssessmentService.assess` 进行 LLM JSON 评估。
2. 高风险词硬信号命中时直接返回 HIGH。
3. LLM 失败时回退到关键词启发式规则（`heuristic`）。
4. `RISK` 意图时强制提升风险等级至 HIGH。

**输出**：`assessment`、`risk_level`

---

### 5. CompanionAgent（`_companion_node`）

**职责**：为 CHAT 意图构建日常陪伴回复。

**行为**：
- 设置 `risk_level = LOW`。
- 组装 system prompt（日常聊天风格）+ 记忆摘要 + 历史消息。

**输出**：`response_messages`

---

### 6. CounselorAgent（`_counselor_node`）

**职责**：为 CONSULT/RISK 意图构建心理支持回复。

**行为**：
- 融合检索知识上下文。
- 通过 `MindBridgeSkillLibrary.response_skill_context` 注入 skill 指引（高风险时叠加 `high_risk_safety_plan`）。
- 组装 system prompt（共情 + 具体支持步骤）+ 记忆摘要 + 历史消息。

**输出**：`response_messages`

---

## 共享辅助函数

| 函数 | 说明 |
|------|------|
| `_classify` | 意图分类（硬规则 + LLM） |
| `_rewrite_query` | 检索查询改写（LLM，失败回退原始输入） |
| `_bounded_model_history` | 历史消息截断（保留首条 system + 最近 N 轮） |
| `_summarize_memory` | 记忆摘要生成（LLM，失败回退确定性摘要） |

---

## 运行入口

### AgentRuntimeService.run()

```python
def run(self, user, session, original_input, model_input) -> AgentRunResult:
    context = AgentContext(user=user, session=session,
                           original_input=original_input, model_input=model_input)
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
```

- `max_steps = 8`，`recursion_limit = 16`。
- DAG 执行完毕后，从 `AgentContext` 提取结果封装为 `AgentRunResult`。

### MindBridgeAgentHarness

harness 层在 `run()` 前后负责：
1. 输入脱敏（`PrivacySanitizer`）。
2. 会话解析/创建。
3. 调用 `AgentRuntimeService.run()`。
4. 用户消息持久化（MySQL + Redis）。
5. 心理报告生成（仅 CONSULT/RISK）。
6. Agent trace 保存。
7. 工具计划生成与派发（队列或 MCP client）。
8. 助手消息保存。

---

## Skill 选择策略

| 意图 | 风险 | 选中的 Skill |
|------|------|-------------|
| CHAT | - | 无 |
| CONSULT/RISK | LOW/MEDIUM | 按关键词匹配（焦虑/睡眠/学业/转介） |
| CONSULT/RISK | HIGH | `supportive_response_baseline` + `high_risk_safety_plan` |

---

## 模型配置

`AgentModelRegistry` 支持按 Agent 名字单独覆盖 provider/model/temperature/max_tokens：

- 配置项格式：`AGENT_MODEL_{AGENT_NAME}_MODEL`、`AGENT_MODEL_{AGENT_NAME}_PROVIDER`
- 未配置时回退到全局默认（`ollama_model` / `openai_model`）

---

## 测试

| 测试文件 | 覆盖范围 |
|---------|---------|
| `tests/test_agent_models.py` | `AgentModelRegistry` per-Agent 模型覆盖 |
| `app/harness/runner.py` Agent Routing Suite | CHAT/CONSULT/RISK 路由与步骤序列验证 |

### 路由验证

- **CHAT**：MemoryAgent -> SupervisorAgent -> CompanionAgent
- **CONSULT**：MemoryAgent -> SupervisorAgent -> KnowledgeAgent -> RiskGuardianAgent -> CounselorAgent
- **RISK**：MemoryAgent -> SupervisorAgent -> KnowledgeAgent -> RiskGuardianAgent -> CounselorAgent
