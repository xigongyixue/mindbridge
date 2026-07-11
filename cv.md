MindBridge智能体                        Agent开发/大模型应用                     2026.01 - 2026.04
- 项目简介：面向校园心理场景开发的多Agent智能体平台，支持意图路由、心理知识检索、风险识别与高风险预警闭环。基于LangGraph编排多Agent协作，并通过Agent Runtime Harness统一处理输入脱敏、上下文注入、Skill调度、工具计划生成、风险报告落库与运行trace。
- 技术栈：Python、FastAPI、Harness Engineering、LangGraph、Skill、LoRA、Ollama、Redis、Chroma、MCP、Docker、MySQL、RAG、SQLAlchemy、Basic Auth
- 负责功能：
1. Agent Runtime Harness 设计：设计并实现MindBridgeAgentHarness，将Agent Runtime、数据库落库和工具后处理解耦，统一处理Agent调用、报告生成、运行trace保存、消息持久化和工具计划生成。
2. 多 Agent 协作： 设计基于 CollaborationBlackboard 的事件驱动协作机制，由 CoordinatorAgent 维护任务板，UnderstandingAgent、SafetyAgent、ContextAgent、ResponseAgent 根据能力 claim 任务，发布 intent、risk、context、response proposal 等 artifact，并通过 SafetyAgent 审查后由 CoordinatorAgent 最终采纳。
3. 业务 Skill：设计标准化心理支持Skill体系，动态加载基础支持、高风险安全计划和辅导员交接摘要等 Skill。高风险场景强制叠加安全处理方案，生成辅个案交接摘要，提升回复策略的可审计性和可测试性。
4. 动态路由 RAG：设计 CHAT / CONSULT / RISK 意图路由。普通聊天不触发知识库检索，减少无效召回。基于150条多轮对话样本评测意图路由效果，路由准确率达到97%，其中RISK场景召回率达到99%。
5. Engineering Harness建设：建设一键工程验证Harness，使用临时SQLite和内存短期记忆构造可重复测试环境，覆盖 Risk Safety、Agent Routing、Standard Skills、RAG、API、Tool Queue 六类核心链路。RAG 评测HitRate 为 0.9667，MRR 为 0.9083。
6. MCP与异步任务队列：封装Excel写入、预警发送、备注追加等MCP工具，并实现异步工具队列处理高风险后置任务。队列支持幂等创建、限流、失败重试和dead letter，避免工具调用阻塞学生端流式回复。