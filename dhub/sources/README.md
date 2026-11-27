# Sources

数据源层提供版本化的 Confluence 文档、固定 Git 提交和与提交匹配的 CodeGraph。每次聊天或影响分析创建一个独立的 `Sources` 实例。

| 文件 | 职责 |
| --- | --- |
| [manager.py](manager.py) | 选择 Demo 或项目资料，加载 Manifest、固定 Git ref、绑定图适配器和工具，并生成来源快照 |
| [confluence.py](confluence.py) | 定义导出页模型、导出 CLI，以及写入正文和清单的格式 |
| [documents.py](documents.py) | 按页 ID 和版本加载 Markdown，建立标题层级、章节 ID 和检索所需的正文数据 |
| [repositories.py](repositories.py) | 固定 Git 提交，提供安全的目录筛选、内容搜索和原文读取 |
| [codegraph.py](codegraph.py) | 加载适配器，验证索引身份与 SHA，捕获并续读图结果。也包含合成图适配器 |
| [utils.py](utils.py) | 资料错误、相对路径检查、文本读取边界、共用关键词匹配，以及遮盖凭证的单行错误描述 `describe_error` |

资料不可用时保留具体原因。清单行跳过原因写入 `skipped_pages[].reason`，Git 失败附带 Git 的 `fatal:` 说明，CodeGraph 用 `codegraph_reason` 区分未配置、导入失败、工厂失败、索引查询失败和无索引。`manager.py` 在服务端日志中记录各项准备耗时。

## 模块边界

`manager.py` 是组装入口。文档、Git、Confluence 和 CodeGraph 后端通过各自模块导入，资料工具调用这些后端并定义模型接口。直接构建单次调查上下文可用 `from dhub.sources.manager import Sources`。

新增真实数据源时，保留可回溯的页版本、Git SHA 或索引身份，并通过 [tools/](../tools/README.md) 暴露需要的调用方式。内网复用 CodeGraph 与 Confluence 导出时，按实际源码核对适配器边界和返回值。 [内网交接](../../docs/intranet-poc-handoff.md)
