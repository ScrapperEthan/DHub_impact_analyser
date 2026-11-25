# DHub PoC API 页面和 Trace

## 最小运行服务

FastAPI 接收需求并运行 LangGraph。建议单进程后台执行分析，使用 SQLite 保存运行记录、报告和追加式 Trace 事件。开发部署支持 `demo` 和 `project` 两种配置，固定到一次运行的资料快照。演示模式醒目标明使用合成资料，项目模式如实报告数据源状态。

| 方法与路径 | 契约 |
| --- | --- |
| `POST /api/runs` | 接收 `{"requirement":"…"}`，去掉首尾空白后应有文本。创建记录后返回 `202` 和 `{run_id, status}`。空白或格式无效返回 `422` |
| `GET /api/runs/{run_id}` | 返回 `run_id`、`status`、`data_mode`、`source_snapshot`、`report` 和可读的 `error`（如有）。报告生成前 `report` 为 `null` |
| `GET /api/runs/{run_id}/events?after_seq=0` | 返回序号大于 `after_seq` 的事件，按 `seq` 递增。响应包含 `events` 和 `next_after_seq`，空列表沿用游标。可以用固定页长分批读取 |

未知运行返回 `404`。`source_snapshot` 包含知识快照 ID、导入覆盖范围和两个 Repo 的标识、分支、提交及可用性。缺失字段反映为未知或不可用。仓库在 detached HEAD 时分支可以为空，commit SHA 仍用于定位。

| 运行状态 | 含义 |
| --- | --- |
| `queued` / `running` | 已接收，等待执行或调查进行中 |
| `succeeded` | 已完成计划中的调查并生成有效报告。报告可以有调查看过的开放问题 |
| `partial` | 关键调查受限但已有可用报告，例如资料缺失、工具超时、预算用尽或需求范围不足。报告解释缺口 |
| `failed` | 无法交付可用报告。错误与已发生的 Trace 仍可查看 |

前端按 `run_id` 和游标轮询，例如每 1 秒。结束时读完最后一批事件。刷新页面可用相同 `run_id` 重新加载两端数据。后端在同一事务中保存最终报告（如有）、运行终态和终态事件。进程重启中断的运行应收敛到 `partial` 或 `failed`，打开的调用以中断原因结尾，已有事件可回看。

## 影响报告约定

报告由后端以结构化 JSON 交付，前端按字段渲染。

| 字段 | 内容 |
| --- | --- |
| `requirement_summary` 与 `assumptions[]` | 用户目标、适用范围、条件、已采用的假设 |
| `summary` 与 `current_support` | 一句话影响结论。`current_support` 含 `status`、`reason`、`evidence_ids`。状态为 `supported`、`partially_supported`、`change_required` 或 `unknown`，限于当次调查快照 |
| `impact_items[]` | 每项含 `id`、`kind`、`target`、`effect`、`status`、`reason` 和 `evidence_ids`。可带 `related_item_ids` 表示影响路径，例如代码影响指向对应业务规则 |
| `evidence[]` | 已读取的证据引用，结构见下 |
| `regression_scenarios[]` | 情景、触发条件、当前预期、拟议预期和依据。可关联已有测试。证据未确定的预期留空或注明假设 |
| `coverage` 与 `open_questions[]` | 已调查资料、不完整来源和待业务方或开发者确认的问题 |

`kind` 可为 `process`、`business_rule`、`code`、`configuration`、`test`、`downstream` 或 `conflict`。`effect` 可为 `direct_change`（拟议变更直接触及）、`indirect_effect`（输入输出或依赖可能受影响）或 `verify`（需要进一步核对）。影响项的 `status` 可为 `confirmed`、`potential` 或 `unknown`。`confirmed` 表示该影响有当前资料支持，拟议行为以预期表述。`potential` 写明推断和条件，`unknown` 写清所缺证据。已确认的影响和现有能力判断须有能够打开或定位的证据。

每条证据包含 `evidence_id`、`source_type`、`excerpt` 和 `tool_span_id`。证据 ID 在运行内唯一，`tool_span_id` 指向实际返回该证据的工具调用。来源定位如下。

| 来源类型 | 定位字段 |
| --- | --- |
| `confluence` | `page_id`、`version`、`title`、`section` 或带定位的附件引用、`feature_drop`、`source_url`（可用时） |
| `code` | `repo_id`、`branch`（可空）、`commit_sha`、`path`、`start_line`、`end_line`，以及可用的 `symbol` 或提交级 URL。配置和测试采用相同定位 |

后端检查证据 ID、读取来源和快照一致性，并保留足够上下文供人核对。前端在链接不可用时仍展示可复制的版本、章节或行号定位。

## Trace 事件

服务端在执行边界记录事件。真实工具执行开始时写入 `tool.started`，结束时写入 `tool.finished`，两者共享 `span_id`。超时和失败在结束事件中展示。每个 Investigator 的一次派单也有开始和结束，归在主 Agent 调查下面。若用 LangGraph 流式更新对接事件，自定义数据可从节点或工具发出，应用仍以自己持久化的事件为 UI 契约。[LangGraph Streaming](https://docs.langchain.com/oss/python/langgraph/streaming)

| 字段或规则 | 用途 |
| --- | --- |
| `run_id`、`seq`、`at` | 运行 ID、服务端分配的连续递增序号、ISO 8601 时间 |
| `type` | `run.started`、`run.finished`、`agent.started`、`agent.finished`、`tool.started`、`tool.finished` |
| `span_id`、`parent_span_id`、`agent_id` | 关联同一事件对和调用层级。Run 是根。`agent_id` 为 `main`、`domain`、`code`，Run 事件可为空 |
| `status`、`elapsed_ms` | 开始时为 `running`。结束时记录实际结果和耗时。工具结束可为 `succeeded`、`failed`、`timeout` |
| `stage` 或 `tool_name` | Agent 任务摘要或实际工具名。重复调用各有自己的 `span_id` |
| `input_summary`、`output_summary`、`error_code`、`error_summary` | 白名单参数摘要、命中数或结果摘要，以及错误代码和可读说明。零命中为成功结果，资料缺失和超时通过错误字段区分 |

工具结束事件示例为合成数据。

```json
{
  "run_id": "demo-run-1",
  "seq": 9,
  "at": "2026-11-25T03:00:04.520Z",
  "type": "tool.finished",
  "span_id": "tool-3",
  "parent_span_id": "domain-1",
  "agent_id": "domain",
  "tool_name": "read_document",
  "status": "succeeded",
  "elapsed_ms": 24,
  "input_summary": {"page_id": "DEMO-FD2", "version": "1", "section": "Phase 5 / Step 18"},
  "output_summary": {"sections_read": 1, "evidence_ids": ["E1"]}
}
```

Trace 的可见内容限于执行事实和获准展示的业务摘要。服务端在保存事件前过滤凭证、敏感参数和长原文，前端按文本安全显示。工具尚在运行时可看到开始事件，工具失败时同一行显示失败原因。前端按 `seq` 去重并用 `span_id` 合并开始和结束事件。

## 页面要求

- **输入区**提供需求文本框、一条可填入的示例和「开始分析」。创建运行后显示当前状态和资料模式。
- **报告区**把摘要和适用范围放在前面，随后展示影响项、风险或冲突、回归建议和待确认项。用文字标签标识「有证据」「潜在」「待确认」。点击一条证据可查看来源定位，并高亮对应工具调用。
- **Trace 区**按时间显示三个 Agent 的任务和下挂的工具调用。角色用名称、颜色和缩进识别，例如主 Agent 用蓝、业务调查用紫、代码调查用绿。每行显示工具名、状态和时间或耗时，展开后可看到参数摘要、命中数、证据 ID 和错误摘要。
- **布局**桌面可用报告与 Trace 双栏，窄屏上下堆叠。运行中随轮询更新事件，终态仍可滚动和展开。部分结果和失败都应有说明，用户可以修改输入再次发起运行。
