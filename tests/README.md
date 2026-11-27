# Tests

在项目根目录用虚拟环境的 Python 运行测试。Git 相关用例使用临时仓库。网关、Graph provider 和演示资料采用本地样本或模拟实现。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

| 测试文件 | 主要覆盖 |
| --- | --- |
| [test_poc.py](test_poc.py) | 聊天与证据事件、影响分析、规划派单、预算、网关调用与失败原因、Token 合计、配置、运行列表和 Trace 回放 |
| [test_search.py](test_search.py) | Manifest 目录、检索、章节回读和跳过原因，Git 查询范围、截断、长行原文和 Git 失败原因 |
| [test_codegraph.py](test_codegraph.py) | 索引绑定、图窗口续读、覆盖透传、超时、适配器失败分类、工具失败反馈和证据读取范围 |

调整工具、数据源或模块入口时，验证相关的 Agent 调用链和默认演示。使用真实 Confluence、内网网关和复用后的 CodeGraph 验收项目 PoC。 [内网交接](../docs/intranet-poc-handoff.md)
