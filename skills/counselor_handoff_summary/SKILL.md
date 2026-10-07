---
name: counselor_handoff_summary
description: 当心理风险报告生成后，为辅导员或管理员创建面向工作人员的交接摘要时使用。
---

# 辅导员交接摘要

## 工作流程

- 面向辅导员或管理员撰写，而非面向学生。
- 保留学生原始含义，但避免不必要的危险细节。
- 包含报告标识、学生标识、风险等级、情绪标签、置信度、模型摘要、跟进建议以及学生表达的有限摘录。
- 首要跟进行动应涉及当前位置、学生是否有人陪伴以及即时安全。
- 保持交接摘要基于事实且可操作；不要添加诊断或无依据的推测。

## 输出模板

```text
应用 skill: counselor_handoff_summary
报告ID：{{report_id}}
学生：{{student}}
风险等级：{{risk_level}}
情绪标签：{{emotion}}
置信度：{{confidence}}
模型摘要：{{summary}}
建议跟进：
{{next_steps}}
学生原始表达：
{{content_excerpt}}
```
