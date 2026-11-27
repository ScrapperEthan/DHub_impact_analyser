# Reports

[impact.py](impact.py) 定义影响报告、当前支持程度、影响项和回归场景结构。`ground_report()` 核对报告引用的证据 ID 和影响项关联，并按证据可用性调整确认状态。

[Main Agent](../agents/main.py) 负责生成报告和判断证据是否支持结论。[AgentRuntime](../agents/runtime.py) 登记实际读取的证据。报告字段有改动时检查模型输出 Schema、API、回放和报告界面。 [报告字段说明](../../docs/api-ui-trace.md#影响报告约定)
