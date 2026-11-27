# DHub Process Impact Analyser V0

Business 或 Developer 输入拟议变更，网页实时显示调查步骤和计划，完成后呈现结构化影响报告。默认演示使用合成的 Confluence 页面、示例 Git 仓库和静态调用关系。内网已接通 Confluence 导出和 LLM，项目 PoC 的下一步是复用真实 CodeGraph 并跑通证据链。 [内网交接](docs/intranet-poc-handoff.md)

## 当前 V0 工作流

在线聊天和影响分析共用 **Main Agent、Adaptive Planning、调查派单和证据登记**。每次运行先绑定 Confluence 文档快照和 Git 提交。主 Agent 可以直接答复、按需调查，或用 `update_plan` 建立显式计划。发现新的范围或证据缺口时，可以在当次运行内创建、扩展或修订计划，并继续使用已有发现。聊天交付自然语言回复，影响分析交付经过结构和证据引用校验的报告。

每次分析或聊天请求最多执行 50 次资料工具调用。主 Agent 派单单独计数，上限 50 次，每次派单最多执行 8 次资料工具调用。计划更新有独立预算。运行时限从准备快照开始计算，默认且最高 5 分钟。分析若因预算或超时受限，报告会保留已读取的证据并说明缺口。 [当前流程与预算](docs/agent-spec.md#当前-v0-运行流程) · [LangGraph 源码](dhub/agents/main.py)

## 三个 Agent 和提示词

| Agent | 实现 | 模型提示词 | 工具定义 |
| --- | --- | --- | --- |
| Main Process Impact Analyser | [dhub/agents/main.py](dhub/agents/main.py) | [共享规划与派单](dhub/prompts/main-plan.md)、[影响报告](dhub/prompts/main-report.md)、[对话](dhub/prompts/main-chat.md) | [investigate_domain](dhub/tools/investigate_domain.py) · [investigate_code](dhub/tools/investigate_code.py) · [update_plan](dhub/tools/update_plan.py) |
| Domain Knowledge Investigator | [dhub/agents/domain.py](dhub/agents/domain.py) | [业务调查](dhub/prompts/domain.md) | [Domain 工具注册](dhub/tools/domain.py) |
| Code Investigator | [dhub/agents/code.py](dhub/agents/code.py) | [代码调查](dhub/prompts/code.md) | [Code 工具注册](dhub/tools/code.py) |

模型模式将共用指令、决策基线和角色提示词组合后发送给网关。 [共用提示词](dhub/prompts/common.md) · [工具契约](docs/expected-tools.md)

文档搜索可按 FD、页面和版本过滤，按相关性返回有章节 ID 的命中。代码搜索支持字面和标识符变体匹配，按目录、glob 和扩展名限定固定提交中的文件。用 `list_documents` 和 `list_files` 翻查目录，用原文工具登记证据。搜索结果和 Trace 都会说明覆盖与截断。 [搜索状态](docs/expected-tools.md#搜索状态)

Main 用版本化文档了解业务规则和设计意图，用固定 Git 提交中的源码、配置和调用点核实当前实现。发现冲突时，回复会引用两侧来源并说明差异。

### 修改或新增工具

每个模型工具在 `dhub/tools/<工具名>.py` 中定义，例如 `search_code.py` 或 `read_document.py`。实现的签名、默认值和 docstring 决定模型可见的接口。 [全部工具文件](docs/expected-tools.md#1-tool-overview)

新增资料工具时，在对应文件实现查询和 `TRACE_FIELDS`，再绑定到所属 Investigator 的 `toolset()`。需要生成证据的工具同时提供 `evidence_factory`。通用路径、查询、分页和派单函数放在 [tools/utils/](dhub/tools/utils/)。

### 其他源码入口

模块职责和依赖关系见 [DHub 源码导读](dhub/README.md)。各核心目录的 README 给出具体文件和扩展入口。

| 职责 | 目录与文件 |
| --- | --- |
| 单次运行的资料快照和工具组装 | [sources/manager.py](dhub/sources/manager.py) · [数据源说明](dhub/sources/README.md) |
| 工具执行、证据和预算 | [agents/runtime.py](dhub/agents/runtime.py) |
| 模型与 HTTP 调用 | [llm/](dhub/llm/README.md) |
| 应用和模型配置 | [config/](dhub/config/README.md) · [settings.yaml](dhub/config/settings.yaml) |
| SQLite、Trace 与回放 | [store/](dhub/store/README.md) |
| 影响报告与证据核对 | [reports/](dhub/reports/README.md) |

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

打开 [本地页面](http://127.0.0.1:8000/)，点击 **Use Example**，再点击 **Run Analysis**。默认 `demo + offline` 由内置场景驱动，工具实际读合成原文与源码。其他需求在离线模式下可执行基础检索并返回覆盖说明，接上模型后可自由输入。页面、计划和影响报告使用英文。

报告应定位 **P5 Step 18 Frequency Cap**。离线样例提供四版调查计划，代码首查后新增配置、回归和下游补查。可用 **Previous Version** 和 **Next Version** 查看任务状态和调整原因。点击任务的 **Evidence E1** 等链接可查看证据及其工具调用，刷新当前网址可回放同一运行。展开 **Report History** 可查看最近的运行，点击一项即可打开其报告。

### Agent Chat

点击顶部 **Agent Chat** 进入 [聊天页](http://127.0.0.1:8000/chat)。同一个主 Agent 直接回复，也可按问题调用业务或代码调查。每轮对话在回复前实时显示进行中的调查步骤，Agent 发布计划时可查看任务和修订。回复中的 `[E1]` 等证据 ID 可以点击，打开回复下方 **Sources** 中的原文摘录。左侧 **Sessions** 列出保存在本浏览器中的会话，点击 **New Chat** 开始新会话，点击列表项切换会话。回复默认使用英文，也可按用户要求使用其他语言。模型最多接收当前会话最近 12 轮，并按文本长度裁剪较长的上下文。

默认离线演示可试问 **What is Frequency Cap?**，再追问 **What about Inbound?**。使用下方配置连接现有模型网关后，即可自由聊天和提问。

## 内网配置

应用读入 [settings.yaml](dhub/config/settings.yaml)。可以设置 `DHUB_CONFIG_FILE` 加载站点 YAML，例如 `data/dhub.yaml`。同名变量由进程环境优先覆盖 `.env`，环境配置优先于站点 YAML 和内置默认值。`.env.example` 列出了可用的覆盖项。内网既有配置沿用当前的模型网关与凭证注入方式。 [项目模式示例](docs/intranet-poc-handoff.md#固定仓库并复用-codegraph)

### 模型网关

| 配置项 | 值与含义 |
| --- | --- |
| `LLM_PROVIDER` | `offline` 用于合成演示。`local_responses` 用于 Responses 网关。`openai_chat` 用于 Chat Completions 网关 |
| `LLM_BASE_URL` | `local_responses` 默认为 `http://127.0.0.1:4141/v1`。两种在线 provider 均可填写含版本路径的根地址或完整的协议端点 |
| `DHUB_LLM_MODEL` / `LLM_MODEL` | 填网关目录中可用、支持原生工具调用的模型或部署名。优先使用 DHub 专用值，避免继承其他应用的旧模型 |
| `LLM_API_KEY` | 可选 Bearer Token。由内网环境注入，按网关需要填写 |
| `LLM_TIMEOUT` / `LLM_MAX_TOKENS` | 单次请求超时秒数，默认 40。输出上限分别映射为 Chat Completions 的 `max_tokens` 和 Responses 的 `max_output_tokens` |
| `LLM_REASONING_EFFORT` | 可选。使用网关支持的取值，分别映射为 `reasoning_effort` 和 `reasoning.effort` |
| `DHUB_RUN_TIMEOUT` | 单次分析或聊天请求的运行时限，默认且最高 300 秒。可配置更短时限 |
| `DHUB_LOG_LEVEL` | 服务端控制台日志级别，默认 `INFO`。输出资料准备、CodeGraph、每次 LLM 调用的耗时和网关返回的 Token 数 |

所有 `LLM_*` 设置均可用 `DHUB_LLM_*` 覆盖。网关由配置显式选择。模型不可用时可查本机网关的 `/models` 或 `/v1/models`，把目录中的模型名填入 `DHUB_LLM_MODEL`。

两个在线适配器统一返回 `ChatResult`。Responses 保留工具调用 ID、工具返回和 reasoning 项用于后续轮次，并发送 `store=false`。本机 Responses 直连可用。使用非回环地址时须配置 HTTPS、API key 和 `LLM_ALLOW_NONLOOPBACK=true`。企业 CA 可用 `SSL_CERT_FILE`，Chat Completions 支持 HTTPX 的环境代理。 [Responses](dhub/llm/local_responses.py) · [Chat Completions](dhub/llm/openai_chat.py) · [HTTPX 环境变量](https://www.python-httpx.org/environment_variables/)

### 导入 Confluence

内网已完成 Confluence 拉取，数据位于 `data/confluence/`。按 manifest 使用现有编码文件名的 Markdown 正文。需要重新导出时，可通过导出命令调用内网已有的 provider。 [已有导出如何接入](docs/intranet-poc-handoff.md#确认现有-confluence-导出可检索) · [导出器](dhub/sources/confluence.py)

```powershell
.\.venv\Scripts\python.exe -m dhub.sources.confluence --provider intranet_confluence:pull_pages --space SPACE_KEY --root-page-id ROOT_PAGE_ID --output data/confluence
```

每页提供 `page_id`、`version`、`title`、`markdown`，有条件时带上 `ancestors`、`feature_drop`、`source_url`、更新时间、页面链接和附件元数据。转换时保留标题、表格和条件分支。导出器写 Markdown 与 `manifest.json`。将 `DHUB_CONFLUENCE_MANIFEST` 指向该文件。已有本地导出可按 [演示清单结构](demo/manifest.json) 直接接入，`path` 相对于清单目录。附件当前以元数据线索呈现，可检索正文需要在导出阶段转换为 Markdown。

### 接入项目仓库

Decision Process 的主要实现位于 `decision-engine-instant-decision-svc` 的 `dev` 分支。在内网准备该仓库的本地 Git checkout，并在 `data/repositories.json` 填写实际路径。相对路径按该 JSON 所在目录解析。

```json
[
  {"repo_id": "decision-engine-instant-decision-svc", "path": "D:/dhub/decision-engine-instant-decision-svc", "ref": "dev"}
]
```

将 `DHUB_REPOS_FILE` 指向配置文件。若本地只有远程跟踪分支，可填 `"ref": "origin/dev"`。`ref` 也接受 tag 或 commit，未填时为 `HEAD`。每次分析开始时解析固定提交，然后从版本对象只读搜索与读取。

单仓库配置适用于项目模式。报告按已配置来源和实际可用性说明覆盖范围。Git 搜索和文件读取支持 UTF-8 的 `.sh` 和 `.ps1`。 [代码工具覆盖](docs/expected-tools.md#固定仓库版本与选择调查路径)

### 复用 CodeGraph 实现

在内网从 MDC Assistant 提取已有 CodeGraph Tools 和底层查询、索引读取实现，作为 DHub 项目的可导入依赖。DHub 适配器直接调用这些实现，并将原生的文本或关系记录转成 DHub 工具结果。

用 `DHUB_CODEGRAPH_ADAPTER=module:factory` 指定项目侧工厂，返回满足 `BoundCodeGraphAdapter` 的对象。`index_info` 提供索引对应的 Git SHA 和索引 ID，`explore` 接收已绑定的索引与截止时间。DHub 核对查询前后的索引身份，保存有界结果，并用同次运行的句柄续读。现有数组型适配器可继续按提交 SHA 查询。 [适配器类型](dhub/sources/codegraph.py) · [工具和返回契约](docs/expected-tools.md#34-codegraph_explore)

内网复用时需要当前版 CodeGraph 工具入口、底层查询及索引读取模块，以及一个固定到 `dev` 提交的 Python 索引。索引不可用时可用 Git 搜索和原文继续调查，由 Main 说明图覆盖缺口。 [验收项](docs/poc-acceptance.md#项目资料上的评估)

## 内网验证与排查

可用下面的命令运行本地测试。测试使用演示资料、临时 Git 仓库和模拟网关，覆盖搜索范围与排序、目录翻页、长行与同名章节原文、部分失败、多轮调查、报告证据和运行回放。

`requirements.txt` 固定 LangGraph `1.0.10`、LangChain Core `1.2.31` 及配套的 checkpoint、prebuilt、SDK 和 LangSmith 版本。本地干净环境已验证依赖解析和安装。内网使用目标镜像安装后，可运行 `python -m pip check` 核对该环境。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

- 启动命令仍使用单进程 uvicorn。V0 没有账号隔离，适合本机或经内网接入控制的验证环境。
- 先打开 `/api/health` 确认数据模式 `data_mode`。提交样例后可在网页查看报告，也可通过 `GET /api/runs/{run_id}` 和 `GET /api/runs/{run_id}/events?after_seq=0` 查询。
- `partial` 表示有可用报告但调查受限。`failed` 表示模型没有交付报告，运行的 `error` 带有网关返回的原因。排查时先看服务端日志和 Trace 中失败的工具，再看资料快照中的 `skipped_pages`、仓库 `error` 和 `codegraph_reason`，最后核对模型是否支持原生工具调用。 [失败与部分完成](docs/intranet-poc-handoff.md#区分失败与部分完成)
- `DHUB_DB_PATH` 默认是 `.local/runs.sqlite3`。其中保存分析需求、证据摘要、报告、Trace 和计划版本。转交内网时复制 `dhub/`、`demo/`、`docs/`、`tests/`、`requirements.txt`、`README.md`、`AGENTS.md`、`.env.example` 和 `.gitignore`。`AGENTS.md` 是内网 OpenCode 读取的项目约定。虚拟环境、`data/` 下的实际导出、凭证和运行库在目标环境准备。
- 停止服务用 `Ctrl+C`。将要独立验证的历史请求放入不同数据库文件，便于区分演示和项目运行。

[文档索引与路线图](docs/README.md) · [内网交接](docs/intranet-poc-handoff.md) · [总需求](docs/poc-requirements.md) · [Agent 规格](docs/agent-spec.md) · [API 和 Trace](docs/api-ui-trace.md) · [验收用例](docs/poc-acceptance.md)
