MindBridge 校园心理智能体 Agent 开发/大模型应用 2026.01 - 2026.04

- 项目简介：面向校园心理场景的多 Agent 智能体平台，基于 LangGraph DAG 编排实现意图路由、知识检索、风险评估与高风险预警闭环。通过工程 Harness 统一管理脱敏、持久化、报告生成、工具派发与运行追踪。
- 技术栈：Python、FastAPI、LangGraph、Chroma、Redis、MySQL、SQLAlchemy、Ollama、MCP、Docker、RAG
- 负责功能：
  1. **LangGraph DAG 运行时重构**：将三套运行时（自定义循环、LangGraph 空壳、事件驱动）合并为单一 DAG 流水线（memory → supervisor → \[knowledge → risk\_guardian → counselor | companion]），消除冗余代码，实现 CHAT/CONSULT/RISK 意图的条件路由分流。
  2. **工程 Harness 体系建设**：设计 MindBridgeAgentHarness 将 Agent 运行与基础设施解耦。构建一键验证 Harness，使用临时 SQLite 和内存记忆构造可重复环境，覆盖风险安全、Agent 路由、标准 Skill、RAG、API、工具队列六类链路。RAG 评测 HitRate 0.9667，MRR 0.9083。
  3. **标准化 Skill 体系**：实现可动态加载的心理支持 Skill 体系，包含基础共情、高风险安全计划、焦虑支持、睡眠建议、辅导员交接摘要等 7 个标准化 Skill。按意图与风险等级自动选择 Skill，高风险场景强制叠加安全处理方案。
  4. **记忆压缩与上下文管理**：设计双层缓存架构（Redis 20 条原始消息 + LLM 上下文摘要 + 最近 8 条），实现记忆压缩函数将长对话历史转为「系统摘要 + 近 N 条」，平衡推理质量与 token 成本。
  5. **MCP 工具与异步队列**：封装 Excel 台账、个案创建、预警发送等 MCP 工具。实现异步工具队列，支持幂等创建、滑动窗口限流、失败重试与死信机制，避免工具调用阻塞学生端 SSE 流式回复。
  6. **意图路由与 RAG 优化**：设计「CHAT 跳过检索 + CONSULT/RISK 混合检索」策略，实现硬信号兜底 + LLM 分类两级路由。基于 150 条评测样本验证路由准确率达 97%，RISK 场景召回率 99%。

