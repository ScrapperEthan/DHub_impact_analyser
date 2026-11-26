# DHub Process Impact Analyser V0

Business 或 Developer 输入拟议变更，网页显示三个 Agent 的工具调用和结构化影响报告。默认演示使用合成的 Confluence 页面、两个示例仓库和静态调用关系。内网可配置真实资料与兼容的 LLM 网关。

## 当前 V0 工作流

每次运行先绑定 Confluence 文档快照和 Git 提交。LangGraph 随后按 **Main 规划 → Domain 查业务规则 → Code 查实现 → Main 综合 → 影响报告** 的顺序执行。

Code 首轮直接使用 Domain findings 和初始计划。双方首轮完成后，Main 在综合阶段可按证据缺口选择 Domain 或 Code 补查一次，预算允许时执行，再回到 Main 综合。目标流程还会在首次 Code 调查前由 Main 根据业务发现细化验证任务，这个独立步骤待实现。 [当前流程图与对照](docs/agent-spec.md#当前-v0-运行流程) · [Expected Workflow](docs/expected-workflow.md#3-expected-workflow) · [LangGraph 源码](dhub/analyzer.py)

## 三个 Agent 和提示词

| Agent | 实现 | 模型提示词 | 工具定义 |
| --- | --- | --- | --- |
| Main Process Impact Analyser | [dhub/agents/main.py](dhub/agents/main.py) | [需求规划](dhub/prompts/main-plan.md)、[影响报告](dhub/prompts/main-report.md) | — |
| Domain Knowledge Investigator | [dhub/agents/domain.py](dhub/agents/domain.py) | [业务调查](dhub/prompts/domain.md) | [dhub/tools/domain.py](dhub/tools/domain.py) |
| Code Investigator | [dhub/agents/code.py](dhub/agents/code.py) | [代码调查](dhub/prompts/code.md) | [dhub/tools/code.py](dhub/tools/code.py) |

模型模式将共用指令、决策基线和角色提示词组合后发送给网关。 [共用提示词](dhub/prompts/common.md) · [九个工具的契约](docs/expected-tools.md)

### 修改或新增工具

业务文档工具集中在 [domain.py](dhub/tools/domain.py)，Git 和 CodeGraph 工具集中在 [code.py](dhub/tools/code.py)。每个工具的方法名、参数类型、默认值和 docstring 对应模型看到的名字、参数和描述。

新增工具时，在所属文件实现方法，再把它加入同一文件的 `toolset()`。用 `trace_fields` 选择 Trace 可展示的参数。需要生成报告证据时，用 `evidence_factory` 提取文档页版本、代码行号等定位字段。

数据加载和 Git 底层操作分别在 [documents.py](dhub/documents.py) 和 [repositories.py](dhub/repositories.py)。[sources.py](dhub/sources.py)为每次分析准备快照，[agent_runtime.py](dhub/agent_runtime.py)统一执行工具、登记证据和记录 Trace。

## 快速启动

在项目根目录打开 PowerShell。需要 Python 3.11 或更新版本，内网项目模式还需要 Git 在 `PATH` 上。浏览器直接访问 FastAPI 托管的页面，前端资源均随项目提供。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn dhub.app:app --host 127.0.0.1 --port 8000
```

在 Linux 上，使用 `python3 -m venv .venv` 和 `.venv/bin/python` 执行后续命令。离线安装可在相同 OS、CPU 架构及 Python 版本的环境下准备 wheelhouse，再通过允许的方式带入内网。

```powershell
python -m pip download --only-binary=:all: -r requirements.txt -d wheelhouse
.\.venv\Scripts\python.exe -m pip install --no-index --find-links wheelhouse -r requirements.txt
```

打开 [本地页面](http://127.0.0.1:8000/)，点「填入示例」后开始分析。默认 `demo + offline` 由内置场景驱动，工具实际读合成原文与源码。其他需求在离线模式下可执行基础检索并返回覆盖说明，接上模型后可自由输入。

报告应定位 **P5 Step 18 Frequency Cap**。Trace 应出现主 Agent、业务调查、代码调查和代码补查，包含 `read_document`、`read_file`、`codegraph_explore`、`codegraph_read_result` 等实际调用。点击报告证据可定位调用，刷新当前网址可回放同一运行。

## 内网配置

从 `.env.example` 复制一份 `.env`。进程环境变量优先于文件。先用 `DHUB_DATA_MODE=demo` 和 `LLM_PROVIDER=openai_chat` 验证网关的原生工具调用。配置下文的文档导出和仓库后，再将 `DHUB_DATA_MODE` 设为 `project`。项目模式需要模型网关。

### 模型网关

| 配置项 | 值与含义 |
| --- | --- |
| `LLM_PROVIDER` | `openai_chat`。V0 使用 Chat Completions 协议，默认演示值为 `offline` |
| `LLM_BASE_URL` | 内网 API 根地址，保留网关要求的版本路径，例如 `https://gateway.example/v1`。也支持完整的 `/chat/completions` 地址 |
| `LLM_MODEL` | 网关上的模型或部署标识。模型需支持 `tools`、`tool_calls`、`role=tool` 和返回 JSON 文本 |
| `LLM_API_KEY` | 可选 Bearer Token。由内网环境注入，按网关需要填写 |
| `LLM_TIMEOUT` / `LLM_MAX_TOKENS` | 单次请求超时秒数，默认 40。可选输出 token 上限，填写时发送 `max_tokens` |
| `DHUB_RUN_TIMEOUT` | 调查及综合的目标时间上限，默认 180 秒。适配器应自行设置调用超时 |

`dhub/llm.py` 延续了 MDC Assistant 的 provider dispatch 和 `chat(messages, tools, temperature)` 接口模式。新协议实现 `ChatClient`，返回统一的 `ChatResult`，然后在 `PROVIDERS` 注册。当前适配器发送非流式 Chat Completions。平台专用鉴权和额外响应协议通过新适配器扩展。企业 CA 可通过 HTTPX 支持的 `SSL_CERT_FILE` 配置。[HTTPX 环境变量](https://www.python-httpx.org/environment_variables/)

### 导入 Confluence

拉取的入口已经预留在 [dhub/confluence.py](dhub/confluence.py)。内网接入人可实现本地模块函数，使用已批准的 Confluence MCP 客户端，按 Space 和根页面抓取所需内容，并产出结构化页面。下面是对接签名和导出命令。

```python
from collections.abc import Iterable
from dhub.confluence import ConfluencePage

def pull_pages(*, space_key: str, root_page_ids: list[str]) -> Iterable[ConfluencePage]:
    """由内网接入人实现，迭代返回 ConfluencePage。"""
    raise NotImplementedError
```

```powershell
.\.venv\Scripts\python.exe -m dhub.confluence --provider intranet_confluence:pull_pages --space SPACE_KEY --root-page-id ROOT_PAGE_ID --output data/confluence
```

每页提供 `page_id`、`version`、`title`、`markdown`，有条件时带上 `ancestors`、`feature_drop`、`source_url`、更新时间、页面链接和附件元数据。转换时保留标题、表格和条件分支。导出器写 Markdown 与 `manifest.json`。将 `DHUB_CONFLUENCE_MANIFEST` 指向该文件。已有本地导出可按 [演示清单结构](demo/manifest.json) 直接接入，`path` 相对于清单目录。附件当前以元数据线索呈现，可检索正文需要在导出阶段转换为 Markdown。

### 接入项目仓库

在内网准备项目仓库的本地 Git checkout。创建 `data/repositories.json`，按实际路径和用于验证的版本填写。相对路径按该 JSON 所在目录解析。

```json
[
  {"repo_id": "dhub", "path": "D:/dhub/project-checkout", "ref": "main"}
]
```

将 `DHUB_REPOS_FILE` 指向配置文件。`ref` 可以是 branch、tag 或 commit，未填时为 `HEAD`。每次分析开始时解析固定提交，然后从版本对象只读搜索与读取，不需要 checkout 其他分支。

项目仓库的语言构成为 94% Python、4% Shell、2% PowerShell。单仓库配置可用于联调。当前覆盖检查按两个仓库判断，因此单仓库成功产出报告时仍会标为 `partial`。Git 搜索和文件读取的扩展名白名单暂未包含 `.sh` 和 `.ps1`。 [代码工具覆盖](docs/expected-tools.md#固定仓库版本与选择调查路径)

CodeGraph 项目适配器通过 `DHUB_CODEGRAPH_ADAPTER=module:factory` 注入。工厂返回满足 [CodeGraphAdapter](dhub/repositories.py) 的对象，接入内网版本的索引查询。`indexed_commit(repo_id)` 返回索引覆盖的提交，`explore(repo_id=..., commit_sha=..., query=...)` 返回可序列化的关系记录数组。后端验证索引与本次提交匹配并提供窗口读取。缺少匹配索引时可以用搜索和带行号的文件阅读继续核实，并在报告中说明覆盖缺口。

## 内网验证与排查

可用下面的命令运行本地烟测。测试使用演示资料和回环模拟网关，覆盖离线流程、原生工具调用、证据链接和运行回放。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

- 启动命令仍使用单进程 uvicorn。V0 没有账号隔离，适合本机或经内网接入控制的验证环境。
- 先打开 `/api/health` 确认数据模式 `data_mode`。提交样例后可在网页查看报告，也可通过 `GET /api/runs/{run_id}` 和 `GET /api/runs/{run_id}/events?after_seq=0` 查询。
- `partial` 表示有可用输出但调查受限。排查时先查看 Trace 中失败的工具和资料快照，再核对模型是否支持原生工具调用。
- `DHUB_DB_PATH` 默认是 `.local/runs.sqlite3`。其中保存需求、证据摘要、报告和 Trace。转交内网时复制 `dhub/`、`demo/`、`docs/`、`tests/`、`requirements.txt`、`README.md` 和 `.env.example`。虚拟环境、实际导出、凭证和运行库在目标环境准备。
- 停止服务用 `Ctrl+C`。将要独立验证的历史请求放入不同数据库文件，便于区分演示和项目运行。

[文档索引与路线图](docs/README.md) · [总需求](docs/poc-requirements.md) · [Agent 规格](docs/agent-spec.md) · [API 和 Trace](docs/api-ui-trace.md) · [验收用例](docs/poc-acceptance.md)
