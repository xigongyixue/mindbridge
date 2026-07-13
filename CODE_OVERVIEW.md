# MindBridge 代码功能说明

本文件对 MindBridge Python 项目中各代码文件的功能进行逐文件说明，便于快速了解代码结构与职责划分。

## 项目概览

MindBridge 是面向校园心理场景的多 Agent 智能体平台，核心能力包括：学生端 SSE 流式聊天、Basic Auth 角色隔离、LangGraph DAG 多 Agent 编排、动态路由 RAG（Chroma 向量库 + BM25 + 本地 reranker）、心理风险评估、后台报告与高风险预警闭环、MCP 工具服务、异步工具队列、RAG 评测与工程 Harness。

技术栈：Python、FastAPI、SQLAlchemy + MySQL、Redis、LangGraph、Chroma、Ollama、OpenAI-compatible API、MCP、Docker、openpyxl、pypdf、原生 HTML/CSS/JS。

---

## 应用入口与基础设施

### `app/main.py`
FastAPI 应用工厂。创建 `FastAPI` 实例，注册中间件（对 `/`、`.html`、`.js`、`.css` 资源设置 `Cache-Control: no-store`），在启动事件中执行数据库 schema 创建、内置数据 seeding、启动工具队列 worker；在关闭事件中停止 worker。挂载 API 路由和 `app/static` 静态文件目录。

### `app/__init__.py`
空包标识文件。

### `app/api/__init__.py`
空包标识文件。

### `app/api/routes.py`
FastAPI 路由集合。定义所有 HTTP 端点：
- `GET /actuator/health`：健康检查。
- `GET /api/profile`：返回当前登录用户信息。
- `POST /api/chat/stream`：学生端 SSE 流式聊天入口；禁止 ROLE_ADMIN 发起对话。
- `GET /api/agent/status`：返回 Agent 框架、模型、Skill、运行时 Harness 状态等元信息。
- `GET /api/reports/me`：学生查看自己的心理报告。
- `GET /api/admin/reports`、`/api/admin/excel-records`、`/api/admin/alerts`、`/api/admin/cases`、`/api/admin/cases/{id}/notes`、`/api/admin/tool-jobs`、`/api/admin/dead-letters`、`/api/admin/agent-traces`、`/api/admin/tool-audits`、`/api/admin/conversations/{session_id}`：管理员后台查询接口。
- `POST /api/admin/knowledge`：追加知识库内容（同步写入 MySQL 与 Chroma）。
- `GET /api/admin/knowledge/status`：知识库与向量库状态。
- `POST /api/admin/knowledge/rebuild-vector`：重建 Chroma 向量索引。
- `POST /api/admin/knowledge/backup`：生成 Chroma 快照。
- `POST /api/admin/knowledge/file`：上传 md/txt/pdf 文件并入库。

---

## 核心层 `app/core/`

### `app/core/config.py`
基于 `pydantic-settings` 的全局配置类 `Settings`，从 `.env` 读取。涵盖：Agent 框架选择与多 Agent 预算、各 Agent 模型 provider/model 覆盖、AI provider（ollama/openai/mock）、Ollama 与 OpenAI 接入参数、本地微调模型资产路径、MySQL/Redis 连接、知识库切块与混合检索权重、Chroma 持久化与快照、RAG 评测、Excel 台账路径、Redis 短期记忆、SMTP 邮件预警、工具队列与限流。提供 `get_settings()` 带缓存的工厂方法。

### `app/core/database.py`
SQLAlchemy 引擎与会话工厂。根据 `DATABASE_URL` 创建 engine（SQLite 时关闭 `check_same_thread`，启用 `pool_pre_ping` 与 `pool_recycle`），暴露 `Base`（DeclarativeBase）、`SessionLocal`、`get_db()` 依赖、`session_scope()` 工厂。

### `app/core/security.py`
Basic Auth 鉴权工具。包含 SHA-256 密码哈希与 `hmac.compare_digest` 常量时间比较、Basic 头解析、`current_user` 依赖（验证用户名密码并返回 `UserAccount`）、`require_admin` 依赖（要求 `ROLE_ADMIN`）。

### `app/core/bootstrap.py`
启动期初始化。`create_schema()` 调用 `Base.metadata.create_all`；`seed_data()` 在数据库为空时创建默认 admin/admin123 与 student/student123 账户，并通过 `KnowledgeService.ensure_source` 把 `app/knowledge/*.md` 同步到知识库。

### `app/core/enums.py`
项目共用的枚举类型：`MessageRole`、`IntentType`(CHAT/CONSULT/RISK)、`RiskLevel`(LOW/MEDIUM/HIGH)、`EmotionLabel`、`ToolStatus`、`ToolJobKind`(EXCEL_REPORT/CASE_CREATE/ALERT_SEND/RISK_ALERT)、`ToolJobStatus`(PENDING/RUNNING/SUCCESS/DEAD)、`RiskCaseStatus`(OPEN/ALERT_SENT/ACKNOWLEDGED)。

---

## Agent 编排层 `app/agents/`

### `app/agents/factory.py`
Agent runtime 工厂。直接返回 `AgentRuntimeService` 实例（统一使用 LangGraph DAG 运行时）。`agent_framework_status()` 暴露 requested/active/langgraphAvailable/fallback 状态供 API 使用。

### `app/agents/runtime.py`
基于 LangGraph StateGraph 的 DAG 运行时（唯一实现）。定义 `AgentStep`、`AgentContext`（在节点间传递的可变状态容器，含意图、风险、记忆摘要、检索知识、回复消息列表等）、`AgentRunResult`、`GraphState`（TypedDict，包装 `AgentContext`），以及 `AgentRuntimeService`（`max_steps=8`）。`run()` 创建 `AgentContext`，调用 `graph.invoke()` 执行 DAG，返回 `AgentRunResult`。

固定 DAG 流水线：
```
memory -> supervisor -> [knowledge -> risk_guardian -> counselor | companion] -> END
```
- `supervisor` 节点后通过 `_route_after_supervisor` 条件边分流：CHAT 走 `companion`，CONSULT/RISK 走 `knowledge -> risk_guardian -> counselor`。

6 个图节点（每个对应一个 Agent 角色）：
- `_memory_node`（MemoryAgent）：从 Redis 加载短期记忆，回退 MySQL，压缩历史并生成记忆摘要。
- `_supervisor_node`（SupervisorAgent）：意图分类（CHAT/CONSULT/RISK），含硬信号兜底。
- `_knowledge_node`（KnowledgeAgent）：把学生输入改写为检索 query 并执行 RAG。
- `_risk_guardian_node`（RiskGuardianAgent）：调用 LLM JSON 评估，含高风险词硬兜底；RISK 意图时强制提升至 HIGH。
- `_companion_node`（CompanionAgent）：CHAT 路径回复方案。
- `_counselor_node`（CounselorAgent）：CONSULT/RISK 路径回复方案，注入知识上下文与 skill 指引。

共享辅助函数（唯一副本）：`_classify`、`_rewrite_query`、`_bounded_model_history`、`_summarize_memory`。

### `app/agents/harness.py`
`MindBridgeAgentHarness` -- 单轮 Agent run 的运行时 harness。统一管理：
- 输入脱敏（`PrivacySanitizer`）。
- 会话解析/创建。
- 调用 `create_agent_runtime().run()`。
- 用户消息持久化（MySQL + Redis）。
- 心理报告 `_create_report`（仅 CONSULT/RISK 写入）。
- `AgentTraceService.save_run` 保存 trace。
- `AgentToolPlan` 生成与 `dispatch_tools`（启用队列时入队，否则走 MCP client）。
- 助手消息保存。

---

## 服务层 `app/services/`

### `app/services/ai.py`
AI 客户端与 prompt 模板。`PromptTemplates` 提供 `intent_prompt`（意图分类）、`psychology_prompt`（JSON 评估）、`answer_system_prompt`（按意图/风险/知识/skill 组装系统提示，高风险时叠加危机处理规则）。`AiClient` 支持 ollama（同步与 SSE 流式）、openai（同步与 SSE 流式）、mock（按 system prompt 内容返回演示文本：JSON 评估、意图标签、高风险安全计划、CounselorAgent/CompanionAgent/KnowledgeAgent 等场景）。还包含高风险词与咨询信号词列表与匹配函数。

### `app/services/chat.py`
`ChatService.stream_chat` —— 学生端 SSE 流式聊天入口。流程：调用 `MindBridgeAgentHarness.run` 得到 outcome → 发送 `meta` 事件 → 调用 `AiClient.stream` 输出 token 事件 → 拼接完整助手内容并保存 → 异步派发工具计划 → 发送 `done` 事件。`sse()` 工具拼装 SSE 报文。

### `app/services/knowledge.py`
`KnowledgeService` —— 知识库与 RAG 检索。支持：
- `ensure_source`：内置 md 内容变化时刷新入库。
- `ingest`/`ingest_file`：切块写入 MySQL 与 Chroma。
- `status`：暴露检索链路、向量可用性、Chroma 配置等。
- `rebuild_vector_index`/`backup_vector_index`：管理员重建与快照。
- `retrieve`：主检索路径，向量召回 + BM25 召回 → `_fuse_and_rerank`（按 `knowledge_hybrid_vector_weight` 与 `knowledge_hybrid_bm25_weight` 融合）→ 本地 reranker（`rerank_score` 综合考虑 hybrid_score、query token 覆盖率、phrase 命中）→ `_expand_best` 对 top1 做邻居扩展。
- 工具函数：`chunk_text`、`bm25_scores`、`hybrid_score`、`tokenize`（中英文+二元 gram）、`token_cosine`、`keyword_score`、`extract_pdf`。
- 向量不可用时通过 `_handle_vector_error` 决定抛错或回退到本地 BM25 + `hybrid_score`。

### `app/services/vector_store.py`
`ChromaKnowledgeStore` —— Chroma 向量库主路径。使用 OpenAI `text-embedding-3-small` 生成 embedding，存入 `chromadb.PersistentClient`。提供 `upsert_chunks`/`sync_chunks`/`has_exact_chunk_ids`/`delete_source`/`query`/`embed_texts`/`snapshot`/`count`。embedding 通过 OpenAI `/embeddings` 接口获取。`snapshot` 会把持久化目录复制到 `chroma_snapshot_dir` 并按 `chroma_snapshot_keep` 保留最近若干份。缺少 API key 或 chromadb 时根据 `knowledge_vector_required` 决定抛错或降级。

### `app/services/assessment.py`
`PsychologicalAssessmentService` —— 心理状态评估。`assess()` 先做高风险词硬信号判断（命中直接返回 HIGH_RISK/HIGH/0.95），否则调用 LLM 返回严格 JSON，解析为 `PsychologyAssessment`；当分数对应的风险等级高于 LLM 标注时按分数提升；emotion 为 HIGH_RISK 时强制 HIGH。LLM 失败时回退到 `heuristic()` 关键词规则。

### `app/services/memory.py`
`RedisShortTermMemoryStore` —— 基于 Redis list 的短期记忆。`load_recent`/`append`/`replace`/`messages_from_rows`，写入时通过 `PrivacySanitizer` 脱敏，TTL 与最大条数由配置控制。`compact_history_for_prompt()` 把长历史压缩为「system 摘要 + 最近 N 条」，摘要使用 `summarize_history_for_memory`（学生近期关注 + 已给过的支持 + 本轮输入关注，不输出诊断或风险等级）。Redis 不可用时返回空列表并打印 warning。

### `app/services/privacy.py`
`PrivacySanitizer` —— 通过正则脱敏手机号、邮箱、身份证号，替换为 `[已脱敏]`。

### `app/services/report.py`
`ReportService` —— 管理员后台查询服务。提供心理报告、Excel 台账、预警记录、风险个案、个案备注、工具任务、死信、Agent trace、工具审计、会话明细等查询，转换为 Pydantic DTO 返回。

### `app/services/skills.py`
`MindBridgeSkillRegistry`/`MindBridgeSkillLibrary` —— 标准化 Skill 体系。从 `skills/*/SKILL.md` 加载（解析 YAML frontmatter + body），支持 `list_skills`/`status_items`/`get_required`/`template_for`/`response_skill_context`/`response_skill_names`（按意图与风险选择 skill：CHAT 不选；HIGH 风险选 `supportive_response_baseline` + `high_risk_safety_plan`；CONSULT 按关键词追加 `anxiety_grounding_support`/`sleep_routine_support`/`academic_stress_planning`/`referral_resource_guidance`）。`counselor_handoff_summary()` 渲染辅导员交接摘要模板。`validation_issues()` 检查目录名一致性、`## Workflow` 小节、description 长度、`counselor_handoff_summary` 必须包含 text 模板。

### `app/services/model_assets.py`
`finetuned_model_status`/`resolve_model_dir` —— 检查本地微调 GGUF 模型资产是否就绪（gguf 文件存在性、大小、Modelfile 存在性），返回给 `/api/agent/status`。

### `app/services/mcp_client.py`
`MindBridgeMcpToolClient` —— MCP client。通过 stdio 启动 `app.mcp_tools.server` 子进程，建立 `ClientSession`，按风险等级依次调用 `mindbridge_excel_report` →（中高风险）`mindbridge_case_create` →（高风险）`mindbridge_alert_send`，解析返回的 caseId 用于后续预警。

### `app/services/tools.py`
`ToolOrchestrationService` —— 工具执行实现。`write_excel` 用 `openpyxl` 写入 Excel 台账（进程内锁串行化、幂等），`create_case` 创建风险个案（含辅导员交接摘要），`send_case_alert` 触发预警并把 case 状态推进为 ALERT_SENT，`acknowledge_case`/`add_case_note` 维护个案状态与备注，`notify` 按 `alert_email_delivery_mode`（log/smtp）投递预警邮件，SMTP 模式支持 TLS/SSL、缺失配置时写入 FAILED 记录。

### `app/services/tool_governance.py`
`ToolPolicyRegistry`/`ToolGovernanceService` —— 工具治理。`ToolPolicy` 定义每种工具允许的风险等级（EXCEL 全等级、CASE_CREATE 中高风险、ALERT_SEND/RISK_ALERT 仅高风险），`authorize()` 校验工具名、报告存在性与风险等级。`ToolGovernanceService.start_job()` 写入 `ToolAuditRecord`（AUTHORIZED/BLOCKED），`require_allowed()` 抛错阻断，`finish()` 更新审计记录。

### `app/services/tool_queue.py`
异步工具队列。`ToolQueueService.enqueue_report()` 按风险等级创建 EXCEL_REPORT/CASE_CREATE/ALERT_SEND 任务，ALERT_SEND 依赖 CASE_CREATE。`RateLimiter` 滑动窗口限流。`ToolQueueWorker` 启动后台 dispatcher 线程，使用 Excel 与 Email 两个 `ThreadPoolExecutor`，依赖未就绪或被限流时 `_requeue`，失败按 `tool_queue_max_attempts` 重试，超限进入 `dead_letter_records`。启动时 `_recover_running_jobs` 把 RUNNING 状态恢复为 PENDING。

### `app/services/trace.py`
`AgentTraceService.save_run` -- 把一次 Agent run 的输入、记忆摘要、agent steps、检索知识、回复消息、评估结果序列化为 JSON 写入 `agent_run_traces` 表。

### `app/services/agent_models.py`
`AgentModelRegistry` —— 按 Agent 名字解析 provider/model/temperature/max_tokens，支持每个 Agent 单独覆盖（如 `AGENT_MODEL_INTENT_MODEL`），未配置时回退到全局默认。`client_for()` 返回配置覆盖后的 `AiClient`。

---

## 数据模型与 DTO

### `app/models/entities.py`
SQLAlchemy ORM 实体。包含：
- `UserAccount`：用户名、显示名、密码哈希、角色（CSV）。
- `ChatSession`/`ChatMessage`：会话与消息。
- `KnowledgeChunk`：知识库分块（source、source_index、content、embedding_json）。
- `PsychologicalReport`：心理报告（intent、emotion、emotion_score、risk_level、confidence、summary）。
- `RiskCase`/`CaseNote`：风险个案与备注。
- `AlertRecord`/`ExcelRecord`：预警与 Excel 台账记录。
- `ToolJob`/`DeadLetterRecord`：工具任务与死信。
- `AgentRunTrace`：Agent 运行 trace。
- `ToolAuditRecord`：工具审计记录。

### `app/schemas/dtos.py`
Pydantic DTO。包含请求/响应模型：`ChatRequest`、`ChatStreamEvent`、`KnowledgeIngestRequest`/`Response`、`ReportResponse`、`ConversationResponse`、`ToolRecordResponse`、`RiskCaseResponse`、`CaseNoteResponse`、`ToolJobResponse`、`DeadLetterResponse`、`AgentRunTraceResponse`、`ToolAuditResponse`、`AiMessage`，以及 `authority()` 辅助函数。

---

## MCP 工具服务 `app/mcp_tools/`

### `app/mcp_tools/server.py`
基于 `FastMCP` 的 MCP server。暴露 6 个工具：
- `mindbridge_excel_report(report_id)`：写入 Excel 台账。
- `mindbridge_case_create(report_id)`：创建/复用风险个案。
- `mindbridge_alert_send(case_id)`：发送或记录预警。
- `mindbridge_alert_ack(case_id, actor, note)`：确认个案。
- `mindbridge_case_note_add(case_id, actor, note)`：追加个案备注。
- `mindbridge_alert_notify(report_id)`：按 report 直接发送预警。

每个工具内部初始化 schema、获取 db session、调用 `ToolOrchestrationService`。可作为 stdio 子进程被 MCP client 调用，也可独立运行。

---

## RAG 评测 `app/rag_eval/`

### `app/rag_eval/runner.py`
RAG 评测脚本。`evaluate()` 加载 `mindbridge-rag-eval.json` 数据集，对每条 case 调用 `KnowledgeService.retrieve`，计算 Recall@K、Precision@K、MRR、NDCG@K、HitRate、平均首次相关排名，输出到 `target/rag-eval-report.json`。

### `app/rag_eval/mindbridge-rag-eval.json`
RAG 评测数据集（150 条），覆盖风险策略、焦虑、低落、睡眠、学业压力、考试季、人际关系、新生适应、咨询转介、隐私边界等主题。

---

## 工程 Harness `app/harness/`

### `app/harness/runner.py`
一键工程验证 Harness。配置环境（临时 SQLite、mock AI、内存短期记忆、关闭向量与队列），重置数据库后依次运行六类 suite：
- **Risk Safety Harness**：高风险/咨询/普通聊天样本，验证报告生成、风险等级、工具队列入队、不暴露后台元数据。
- **Agent Routing Harness**：通过 `MindBridgeAgentHarness` 验证 CHAT/CONSULT/RISK 路由与 Agent 步骤序列。
- **Standard Skills Harness**：验证 7 个标准 Skill 加载、选择逻辑、交接摘要模板渲染。
- **RAG Harness**：基于内置评测集验证 Recall@K、MRR、NDCG、HitRate 阈值。
- **API Harness**：通过 `TestClient` 验证健康检查、认证授权、SSE 聊天、管理员知识库接口。
- **Tool Queue Harness**：验证 Excel/case/alert 依赖、幂等、限流、死信。

报告输出到 `target/harness/harness-report.json` 与 `target/harness/rag-eval-report.json`。

### `app/harness/__init__.py`
包标识文件。

---

## 知识库 `app/knowledge/`

内置校园心理知识库 Markdown 文档（启动时同步到数据库与向量库）：
- `academic-stress-and-burnout.md`：学业压力与倦怠。
- `adjustment-and-transition.md`：新生适应与过渡。
- `anxiety-panic-grounding.md`：焦虑恐慌与 grounding。
- `campus-mental-health.md`：校园心理支持总则。
- `counselor-referral-and-resources.md`：咨询转介与资源。
- `exam-season-guidance.md`：考试季指引。
- `low-mood-depression-support.md`：情绪低落与抑郁支持。
- `relationships-and-family.md`：人际关系与家庭。
- `risk-policy.md`：风险等级策略。
- `sleep-routine-self-care.md`：睡眠作息与自我照顾。
- `privacy-boundaries-and-ethics.md`：隐私边界与伦理。

---

## 标准化 Skills `skills/`

每个子目录包含一个 `SKILL.md`，由 `MindBridgeSkillRegistry` 加载：
- `supportive_response_baseline`：心理咨询与风险回复的基础共情、边界和学生端表达规则。
- `high_risk_safety_plan`：高风险时引导模型优先完成短期安全计划。
- `anxiety_grounding_support`：焦虑、惊恐、崩溃场景的稳定化与 grounding 指引。
- `sleep_routine_support`：失眠、睡眠节律紊乱场景的安全睡眠建议。
- `academic_stress_planning`：考试、作业、论文、绩点压力的下一步拆解。
- `referral_resource_guidance`：校内心理中心、辅导员、可信任支持人和紧急资源转介。
- `counselor_handoff_summary`：生成给辅导员/管理员看的个案交接摘要模板。

---

## 前端 `app/static/`

原生前端页面与资源：
- `index.html`：登录入口，选择学生/管理员账号进入对应工作台。
- `student.html`/`student.js`：学生端聊天界面，SSE 流式接收回复，展示会话状态与快捷表达。
- `admin.html`/`admin.js`：管理员后台，展示报告、风险个案、Excel 台账、预警、Agent trace、知识库状态、知识库上传/重建/备份。
- `app.js`：通用工具（Basic Auth 头、健康检查、登录路由、token 存取）。
- `styles.css`：全局样式。
- `favicon.svg`、`assets/mindbridge-campus-companion.png`：站点图标与品牌图。

---

## 测试 `tests/`

基于 `unittest`，不依赖 pytest：
- `test_memory_compaction.py`：验证 `compact_history_for_prompt` 保留最近消息、注入系统摘要、脱敏手机号、可禁用、摘要长度受限。
- `test_skills.py`：验证 `MindBridgeSkillRegistry` 加载合法 skill、报告 validation warning、缺失 frontmatter 抛错。
- `test_tool_governance.py`：验证 `ToolPolicyRegistry` 按风险等级允许/阻断工具、未知工具被拒。
- `test_privacy_and_assessment.py`：验证脱敏正则、Redis 序列化脱敏、高风险硬信号在调用 LLM 前返回 HIGH。
- `test_agent_models.py`：验证 `AgentModelRegistry` 支持 per-Agent 模型覆盖（如 IntentAgent 使用独立模型，CounselorAgent 回退全局默认）。

---

## 脚本 `scripts/`

- `run-dev.sh`：设置默认环境变量后启动 `uvicorn app.main:app`。
- `start-ollama.sh`：检测并启动 Ollama 服务（支持 macOS 应用路径）。
- `create-finetuned-model.sh`：检查 GGUF 文件（支持 `UPSTREAM_GGUF` 软链接），调用 `ollama create` 生成微调模型。
- `package-release.sh`：打包发布 tar.gz（排除 .git、.env、data、gguf、密钥等敏感与运行时文件）。

---

## 模型资产 `models/mindbridge-qwen2.5-7b-ft/`

- `Modelfile`：Ollama 模型定义，基于本地 GGUF，配置 Qwen2.5 chat 模板、MindBridge 系统提示（温和、非评判、不输出后台标签、高风险引导联系可信任的人或紧急救助）、temperature 0.35、top_p 0.85、repeat_penalty 1.12、num_predict 512、stop 序列。
- 实际 GGUF 文件需另行放置：`mindbridge-qwen2.5-7b-ft-q4_k_m.gguf`。

---

## 部署与配置

### `Dockerfile`
基于 `python:3.12-slim`，安装 build-essential 与 curl，pip 安装 `requirements.txt`，拷贝 `app/` 与 `models/.../Modelfile`，暴露 8080，启动 uvicorn。

### `docker-compose.yml`
三个服务：
- `mysql:8.0`：utf8mb4，宿主机映射 13306。
- `redis:7.2-alpine`：宿主机映射 16379。
- `app`：build `.`，依赖 mysql/redis healthy，环境变量注入 Agent 框架、AI provider、Ollama、数据库、Redis、知识库向量、工具队列、Excel 台账路径等，挂载 `./data` 用于持久化 Chroma 与 Excel。

### `requirements.txt`
固定版本依赖：fastapi、uvicorn[standard]、sqlalchemy、pymysql、cryptography、redis、pydantic-settings、langchain-core、langgraph、chromadb、python-multipart、httpx、openpyxl、pypdf、mcp（Python ≥ 3.10）。

### `.env.example`
全量配置示例：Agent 框架、AI provider、Ollama/OpenAI、数据库、Redis、知识库与向量、Chroma、RAG 评测、工具队列、SMTP、邮件预警等。

### `.github/workflows/test.yml`
GitHub Actions CI。在 ubuntu-latest + Python 3.12 上安装依赖，`compileall` 检查语法，以 mock AI、SQLite、Redis 15 库、关闭向量方式运行 `python -m unittest discover -s tests`。

### `.gitignore` / `.dockerignore`
分别用于 git 与 docker build 排除运行时与敏感文件。

---

## 模块依赖关系概览

```
HTTP 请求
  -> app/main.py (FastAPI app)
    -> app/api/routes.py (路由)
      -> app/services/chat.py (SSE 流式聊天)
        -> app/agents/harness.py (MindBridgeAgentHarness)
          -> app/agents/factory.py (创建 runtime)
            -> app/agents/runtime.py (LangGraph DAG runtime)
              -> app/services/ai.py (LLM 调用)
              -> app/services/knowledge.py (RAG 检索)
              -> app/services/memory.py (短期记忆)
              -> app/services/assessment.py (风险评估)
              -> app/services/skills.py (Skill 加载)
          -> app/services/privacy.py (脱敏)
          -> app/services/trace.py (trace 落库)
          -> app/services/tool_queue.py 或 mcp_client.py (工具派发)
            -> app/services/tools.py (Excel/case/alert 执行)
            -> app/services/tool_governance.py (工具审计)
          -> app/models/entities.py + app/core/database.py (持久化)
```
