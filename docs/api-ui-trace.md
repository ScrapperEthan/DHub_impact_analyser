# DHub PoC API 页面和 Trace

## 最小运行服务

FastAPI 接收需求并运行 LangGraph。单进程后台执行分析，SQLite 保存运行记录、报告、Trace 和计划版本。开发部署支持 `demo` 和 `project` 两种配置，固定到一次运行的资料快照。演示模式醒目标明使用合成资料，项目模式如实报告数据源状态。

| 方法与路径 | 契约 |
| --- | --- |
| `POST /api/runs` | 接收 `{"requirement":"…"}`，去掉首尾空白后应有文本。创建记录后返回 `202` 和 `{run_id, status}`。空白或格式无效返回 `422` |
| `GET /api/runs?limit=50` | 按创建时间倒序返回 `{"runs":[…]}`，每项含 `run_id`、`requirement`（前 300 字符）、`data_mode`、`status`、`created_at`、`finished_at`，不含报告和 Trace。`limit` 为 1–200 |
| `GET /api/runs/{run_id}` | 返回 `run_id`、`status`、`data_mode`、`source_snapshot`、`report` 和可读的 `error`（如有）。报告生成前 `report` 为 `null` |
| `GET /api/runs/{run_id}/events?after_seq=0` | 返回序号大于 `after_seq` 的事件，按 `seq` 递增。响应包含 `events` 和 `next_after_seq`，空列表沿用游标。可以用固定页长分批读取 |
| `GET /api/runs/{run_id}/stream?after_seq=0` | 随运行逐条发送同一事件序列。收到终态事件后结束，可用最后处理的 `seq` 恢复读取 |
| `POST /api/chat` | 接收 `{"message":"…","history":[{"user":"…","assistant":"…"}]}`，完成后返回 `{"reply":"…"}`。`history` 可省略 |
| `POST /api/chat/stream` | 接收同一聊天请求，逐条发送本轮调查、计划和终态事件 |

未知运行返回 `404`。`source_snapshot` 包含知识快照 ID、导入覆盖范围和仓库列表，每条记录含仓库标识、分支、提交及可用性。缺失字段反映为未知或不可用。仓库在 detached HEAD 时分支可以为空，commit SHA 仍用于定位。

| 运行状态 | 含义 |
| --- | --- |
| `queued` / `running` | 已接收，等待执行或调查进行中 |
| `succeeded` | 已交付有效报告。报告可以包含覆盖范围内仍需业务方确认的问题 |
| `partial` | 关键调查受限但已有可用报告，例如资料缺失、工具超时、预算或运行时限用尽、需求范围不足。报告解释缺口 |
| `failed` | 模型没有交付报告，例如网关错误、连接失败、请求超时或报告两次不符合结构。`error` 写明原因并遮盖凭证。失败前登记的证据保留在 `report.evidence`，Trace 仍可查看 |

流式接口使用 `application/x-ndjson`，每个 UTF-8 JSON 对象占一行。分析页断线后从最近的 `seq` 继续，刷新可用相同 `run_id` 回放计划与 Trace。服务端在同一事务中保存报告（如有）、运行终态和 `run.finished`。进程重启后，中断运行进入 `failed`，打开的调用以中断原因结尾，已有事件可回看。

聊天事件按到达顺序处理，终态为 `{"type":"chat.completed","reply":"…"}` 或 `{"type":"chat.error","status":502,"detail":"…"}`，其中 `status` 是语义状态码。本轮登记了证据时，终态前先发送 `{"type":"chat.evidence","evidence":[…]}`，结构与报告证据相同，`excerpt` 最多 2000 字符，超出时标记 `excerpt_truncated`。证据 ID 在每轮聊天内从 E1 编号。请求体校验仍返回 HTTP `422`。聊天页面把会话列表和问答文本（含各轮证据）保存在浏览器 `localStorage`，每个标签页记住当前会话。调查步骤和计划只在当前页面显示。最终聊天文本在校验后随 `chat.completed` 发送，分析报告随终态从运行快照读取。

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

每条证据包含 `evidence_id`、`source_type`、`excerpt` 和 `tool_span_id`。证据 ID 在运行内唯一，`tool_span_id` 指向实际返回该证据的工具调用。由显式任务派单收集的证据还带有 `task_id`。来源定位如下。

| 来源类型 | 定位字段 |
| --- | --- |
| `confluence` | `page_id`、`version`、`title`、`section_id`、起止行号、章节内字符偏移、`feature_drop`、`source_url`（可用时） |
| `code` | `repo_id`、`branch`（可空）、`commit_sha`、`path`、`start_line`、`end_line`、`column_offset`、`end_column_offset`。配置和测试采用相同定位 |

后端检查证据 ID、读取来源和快照一致性，并保留足够上下文供人核对。前端在链接不可用时仍展示可复制的版本、章节或行号定位。

原文工具与证据条目使用同一读取窗口。有脱敏时使用等长掩码并标记 `redacted`。代码证据字段中的字符偏移从 0 开始，终点为开区间。Main 收到的证据预览另以 `excerpt_truncated` 标记。

## Trace 事件

服务端在执行边界记录事件。真实工具执行开始时写入 `tool.started`，结束时写入 `tool.finished`，两者共享 `span_id`。超时和失败在结束事件中展示。每次 Investigator 派单记录为主 Agent 下的 `agent.started` 和 `agent.finished`，`input_summary.question` 保存派单问题，`input_summary.task_id` 保存有关联的计划任务 ID。

| 字段或规则 | 用途 |
| --- | --- |
| `run_id`、`seq`、`at` | 分析事件附有运行 ID、连续递增序号及 ISO 8601 时间 |
| `type` | `run.started`、`run.finished`、`agent.started`、`agent.finished`、`tool.started`、`tool.finished`、`plan.updated`、聊天的 `chat.evidence` 或聊天终态 |
| `span_id`、`parent_span_id`、`agent_id` | 关联同一事件对和调用层级。Run 是根。`agent_id` 为 `main`、`domain`、`code`，Run 事件可为空 |
| `status`、`elapsed_ms` | 开始时为 `running`。结束时记录实际结果和耗时。工具结束可为 `succeeded`、`partial`、`failed` 或 `timeout` |
| `stage` 或 `tool_name` | Agent 任务摘要或实际工具名。重复调用各有自己的 `span_id` |
| `input_summary`、`output_summary`、`error_code`、`error_summary` | 白名单参数、结果及错误摘要。搜索摘要有实际范围、返回数、截断状态、`has_more` 和可用性警告。来源全部不可用时，工具 Trace 为 `failed`，搜索摘要为 `unavailable` |

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
  "output_summary": {"section_id": "sec-5", "start_line": 5, "end_line": 9, "truncated": false, "next_offset": null, "evidence_ids": ["E1"]}
}
```

Trace 的可见内容限于执行事实和获准展示的业务摘要。服务端在保存事件前过滤凭证、敏感参数和长原文，前端按文本安全显示。工具尚在运行时可看到开始事件，工具失败时同一行显示失败原因。前端按 `seq` 去重并用 `span_id` 合并开始和结束事件。

CodeGraph 查询与续读用 `result_ref` 关联 Trace。Trace 记录来源名称、返回量、捕获量、续读状态、截断和警告数量。原始查询、图正文、provider 警告文本和可续读的 `result_id` 留在调查工具调用中。`retrieval_coverage` 将实际搜索和图状态传给 Main。

工具失败时，Trace 行在状态旁显示 `error_code`，展开后显示 `error_summary`。调查员收到同一失败的结构化结果 `{error, message, tool, status, input, hint}`，其中 `input` 是 Trace 白名单参数，`hint` 给出改正参数、换用工具或记录缺口的建议。Investigator 的 findings 带 `tool_errors`，列出本次派单中失败的工具、错误码、说明和参数摘要，供 Main 重试、改派或更新计划。所有错误文本都经过与 Trace 相同的脱敏。

## 调查计划事件

接受新计划、修订计划或新增任务证据时，`plan.updated` 携带完整快照。其 `span_id` 指向所属 Main，版本从 1 开始，在该次请求内递增。每个任务的 `evidence_ids` 来自本次运行实际登记的证据。历史事件可能缺少该字段。采用直接答复或隐式规划时，页面随实际调用展示 Trace。

```json
{
  "type": "plan.updated",
  "agent_id": "main",
  "span_id": "main-1",
  "plan": {
    "version": 3,
    "goal": "Assess the impact of removing the Frequency Cap exemption for Outbound Mandatory Campaigns",
    "scope": {"Affected channel": "Outbound", "Regression scope": "Existing Inbound behavior"},
    "candidate_steps": ["P5 Step 18 · Frequency Cap"],
    "tasks": [
      {"id": "T1", "agent": "domain", "question": "Verify the exemption rule", "status": "completed", "evidence_ids": ["E1"]},
      {"id": "T2", "agent": "code", "question": "Check the shared implementation", "status": "completed", "evidence_ids": ["E2", "E3"]},
      {"id": "T3", "agent": "code", "question": "Review configuration and regression assertions", "status": "in_progress", "evidence_ids": []}
    ],
    "reason": "Both channels use the same implementation. Check whether the change can be isolated to Outbound."
  }
}
```

字段约束和任务状态见 [update_plan](expected-tools.md#12-update_plan)。计划描述待答问题及其状态，报告引用本次运行实际读取的证据。

## 页面要求

- **输入区**提供 **Change Request** 文本框、**Use Example** 和 **Run Analysis**。创建运行后显示当前状态和资料模式。
- **Report History** 是可收起的列表，显示最近 50 次运行的状态、需求、资料模式和时间。点击一项载入该运行的报告、计划和 Trace，当前运行高亮。
- **聊天页**左侧列出已保存的会话，**New Chat** 开始新会话。回复中的 `[E3]` 等证据 ID 可点击，打开回复下方 **Sources** 中的原文摘录。当前页面产生的回复还可用 **View Tool Call** 跳到对应工具调用。
- **报告区**把摘要和适用范围放在前面，随后展示影响项、风险或冲突、回归建议和待确认项。用 **Evidence-backed**、**Potential** 和 **Unverified** 标识证据状态。点击一条证据可查看来源定位，并高亮对应工具调用。
- **Trace 区**按时间显示三个 Agent 的任务和下挂的工具调用。角色用名称、颜色和缩进识别，例如主 Agent 用蓝、业务调查用紫、代码调查用绿。每行显示工具名、状态和时间或耗时，展开后可看到参数摘要、命中数、证据 ID 和错误摘要。
- **调查计划**展示版本、完成计数、范围、候选步骤、任务状态、修订原因和证据链接。用 **Previous Version** 和 **Next Version** 回看变化。聊天页的证据链接指向对应工具调用。
- **布局**以桌面浏览器为设计和验收目标，采用报告与调查双栏布局。窄屏适配不属于 V0 验收要求。逐事件更新进度，终态可滚动和展开。部分结果和失败保留相应说明，用户可以修改输入再次发起运行。
