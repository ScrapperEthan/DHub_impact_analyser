# Store

本目录提供分析运行记录和 Trace 回放的 SQLite 存储。默认路径为项目内 `.local/runs.sqlite3`，可用配置调整。

| 文件 | 职责 |
| --- | --- |
| [runs.py](runs.py) | 初始化数据库，推进运行状态，保存资料快照、报告和错误，按序读取事件，并为 Report History 列出最近的运行 |
| [events.py](events.py) | 为 Trace 写入序号和时间，保存允许展示的字段，在运行结束时闭合剩余 span |

[app.py](../app.py) 使用 `RunStore` 持久化运行。工具和证据的登记先在 [AgentRuntime](../agents/runtime.py) 完成。修改事件可见字段时，同步核对存储、回放和页面契约。 [API 与 Trace](../../docs/api-ui-trace.md)
