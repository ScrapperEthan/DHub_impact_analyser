# Prompts

[load_system_prompt](../agents/runtime.py) 先加载 `common.md` 和 `glossary.md`，再按调用场景拼接角色提示词。

| 文件 | 用途 |
| --- | --- |
| [common.md](common.md) · [glossary.md](glossary.md) | 共享证据规则、Decision Process Baseline、系统概览和术语导航 |
| [main-plan.md](main-plan.md) | 聊天与报告共用的自适应规划、派单和来源核对 |
| [main-chat.md](main-chat.md) | 对话回复的格式和调查要求 |
| [main-report.md](main-report.md) | 影响报告的证据和状态要求。Main 在运行时附加 JSON Schema |
| [domain.md](domain.md) · [code.md](code.md) | 两位调查员的搜索策略、原文证据及发现格式 |

事实结论需要回到对应页版本或固定提交核对。文档描述与实现存在差异时，Main 按当前源码、配置和实际调用条件判断实现，并引用两侧来源。工具参数或输出变更时，检查使用该工具的角色提示词。 [Agent 职责](../agents/README.md)
