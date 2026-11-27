# Tools

每个模型可调用工具放在同名 `.py` 文件中。工具函数的签名、默认值和 docstring 决定模型接口，显式参数模型可以通过 `args_schema` 提供。

| 使用方 | 工具与注册入口 |
| --- | --- |
| Main | [investigate_domain](investigate_domain.py)、[investigate_code](investigate_code.py)、[update_plan](update_plan.py)，由 [Main](../agents/main.py) 绑定到本次调查 |
| Domain | [list_documents](list_documents.py)、[search_documents](search_documents.py)、[read_document](read_document.py)、[get_document_metadata](get_document_metadata.py)，见 [DomainTools](domain.py) |
| Code | [get_repository_metadata](get_repository_metadata.py)、[list_files](list_files.py)、[search_code](search_code.py)、[read_file](read_file.py)、[codegraph_explore](codegraph_explore.py)、[codegraph_read_result](codegraph_read_result.py)，见 [CodeTools](code.py) |

[registry.py](registry.py) 将函数绑定为 `ToolDefinition`。`TRACE_FIELDS` 选择可进入 Trace 的字段，读取原文的工具由 `evidence_factory` 产生证据。`utils/` 包含工具专用的分页、范围检查、图窗口校验和派单处理。

新增资料工具时，在同名文件实现调用和参数，将它注册到所属 Investigator。具体快照、路径和原文访问使用 [sources/](../sources/README.md)，执行预算和证据登记由 [AgentRuntime](../agents/runtime.py) 处理。 [工具契约](../../docs/expected-tools.md)
