# Agents

本目录维护三位 Agent 的职责、Main 的调查计划，以及共享的执行过程。聊天和影响分析由 [analyzer.py](../analyzer.py) 创建同一种运行时。

| 文件 | 职责 |
| --- | --- |
| [main.py](main.py) | 运行 Main 的 LangGraph 循环，规划、派单、补查并形成回复或影响报告 |
| [domain.py](domain.py) | 在文档快照里调查业务规则、例外和版本差异，返回证据和代码线索 |
| [code.py](code.py) | 针对固定提交调查实现、配置、测试和依赖，返回源码证据与覆盖说明 |
| [runtime.py](runtime.py) | 拼接提示词，执行模型和工具调用，控制预算与期限，维护 Trace、搜索历史和证据。记录 LLM 耗时与 Token，把工具失败以 `tool_errors` 交回 Main |
| [planning.py](planning.py) | 定义计划、任务状态、稳定任务 ID 和 `update_plan` 参数校验 |

Main 的模型调用失败或报告两次不合结构时，`runtime.failure` 记录原因，运行为 `failed`。时限、预算和资料缺口只记入 `limits`，运行为 `partial`。

调查策略放在角色提示词中。查资料时从运行时提供的单次快照获取工具。修改计划字段时，同时核对 Main、`update_plan`、计划事件和现有回放测试。 [提示词](../prompts/README.md) · [工具](../tools/README.md) · [计划与 Trace 契约](../../docs/api-ui-trace.md)
