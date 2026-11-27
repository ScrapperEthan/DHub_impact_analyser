# DHub PoC 内网交接

**给内网 OpenCode。** Confluence 导出和 LLM 网关已在内网接通。复用现有导出、网关配置与凭证注入。现在需要把真实仓库和同一提交的 CodeGraph 索引绑定到 DHub，再用真实需求跑通文档、图调查、源码证据和影响报告。

## 合并本轮目录调整

| 要找的实现 | 入口 |
| --- | --- |
| 模型可调用工具 | `dhub/tools/<工具名>.py`，如 [search_code.py](../dhub/tools/search_code.py)、[codegraph_explore.py](../dhub/tools/codegraph_explore.py) |
| 工具绑定与共用逻辑 | [Code 注册](../dhub/tools/code.py) · [Domain 注册](../dhub/tools/domain.py) · [工具辅助函数](../dhub/tools/utils/) |
| Confluence 导出与快照加载 | [confluence.py](../dhub/sources/confluence.py) · [documents.py](../dhub/sources/documents.py) |
| 单次运行的数据和工具组装 | [sources/manager.py](../dhub/sources/manager.py) |
| 模型实现与注册 | [llm/](../dhub/llm/) · [PROVIDERS 与 create_client](../dhub/llm/__init__.py) |
| 设置与默认值 | [settings.py](../dhub/config/settings.py) · [settings.yaml](../dhub/config/settings.yaml) |
| SQLite 和 Trace 回放 | [runs.py](../dhub/store/runs.py) · [events.py](../dhub/store/events.py) |

合并时把内网新增的 LLM provider 放入 `dhub/llm/`，在 `PROVIDERS` 中注册。Confluence 接入代码和导出配置合并到 [数据源目录](../dhub/sources/README.md)，已有的独立 provider 通过原来的配置绑定。内网扩展若引用了迁移前的模块，请改用以下入口。

| 引用内容 | 当前 Python 模块 |
| --- | --- |
| `ConfluencePage`、`write_export` 和导出 CLI | `dhub.sources.confluence` |
| `BoundCodeGraphAdapter`、`CodeGraphIndex` 和 `CodeGraphResult` | `dhub.sources.codegraph` |
| `Knowledge` 与 `Repository` | `dhub.sources.documents` 与 `dhub.sources.repositories` |
| `SourceError`、资料路径检查与文本读取 | `dhub.sources.utils` |

Confluence 导出命令入口是 `python -m dhub.sources.confluence`。模型、配置和存储继续通过 `dhub.llm`、`dhub.config` 和 `dhub.store` 导入。

## 原定 Confluence 知识库

V0 原文知识库原定放在项目的 `data/confluence/`。导出器把页版本的 Markdown 存到 `pages/`，用同层的 `manifest.json` 作目录。每条目录记录用 `page_id` 加 `version` 唯一标识页面正文。 [原始需求](poc-requirements.md#dhub-资料与决策基线) · [知识准备](agent-spec.md#知识准备与检索) · [导出器](../dhub/sources/confluence.py)

```text
data/confluence/
├── manifest.json            # 页版本目录，指向 pages 中的 Markdown
└── pages/
    ├── <编码文件名 A>.md     # Confluence 页正文
    └── <编码文件名 B>.md
```

逻辑目录沿用 Confluence 层级。用 `feature_drop` 表示 FD1–FD5，用 `ancestors` 保留从 Space 根到父页面的标题路径。例如 `Project Management → Core Product - FD2 → FD2 Requirements` 下的 `Decisioning Process`。Agent 可按 FD 或父页面过滤，再按 Markdown 标题定位章节。

| 原定收录范围 | 页面内容 |
| --- | --- |
| FD1–FD5 的 Requirements 与相关变更 | 业务需求、规则及适用范围、条件和例外、跨 FD 的变更、验收条件 |
| FD2 的 Decisioning Process | 六阶段、21 步的初始导航，以及各步的说明。如 P5 Step 18 的 Frequency Cap 与豁免条件 |
| Core Component Documentation 及相关设计页 | 组件职责、架构或设计说明、配置语义，以及用于核对规则的相关页面 |
| 页面链接与附件 | 保留链接、流程图和附件元数据。获准纳入检索的附件正文需要可读的 Markdown |

一份 Markdown 对应一个页面版本，保留原标题和子标题层级，以及各小节下的规则正文、表格、验收标准、适用条件和例外。目录记录还可带源 URL、更新时间、获准提供的更新人、链接及附件元数据。`list_documents` 用目录导航，`search_documents` 定位章节，`read_document` 读出可引用的原文窗口。

Decision Process Baseline、DHub System Overview 与 Glossary 的简版目前供 Agent 导航。调查规则和例外时回读 Confluence 原文，判断实现时核对固定提交的源码。 [共享背景](../dhub/prompts/common.md) · [术语表](../dhub/prompts/glossary.md)

### 主题 Wiki 目录设计

主题 Wiki 放在 `data/knowledge/wiki/`，用于按业务主题汇总术语、规则、例外和跨 FD 变化，帮助调查员找到对应的原始页面。目录预留如下。文件名用于说明后续组织方式。

```text
data/knowledge/wiki/
├── index.md                   # 主题目录和导航
└── topics/
    ├── eligibility.md
    ├── contact_policy.md
    ├── suppression_rules.md
    └── frequency_cap.md
```

主题页可包括业务概览、Decision Process 位置、规则与适用条件、例外、各 FD 变化、相关组件和待核实项。每条业务规则都要附原始 Confluence 的 `page_id`、`version`、章节定位和链接。报告引用以 `data/confluence/` 的版本化原文为依据，调查员通过 Wiki 找到线索后回读原文核实。

`data/` 在 `.gitignore` 中，这个目录不会随代码交接。需要时在内网 DHub 根目录创建 `data/knowledge/wiki/` 和 `topics/`。当前代码不读取 Wiki，空目录不影响运行。

本阶段预留目录，并优先验收原文导入、检索、读取和证据溯源。后续可评估用 **OKF Generate Skill** 辅助 OpenCode 把 Confluence 原文整理成主题页，核对规则和原文引用后再使用。Wiki 内容生成和专用检索工具属于后续评估项。 [知识路线图](README.md#知识库分期)

## 确认现有 Confluence 导出可检索

内网已提供 `data/confluence/pages/` 和 `data/confluence/manifest`。`pages/` 中有编码文件名的 `.md` 和 `.html`。以 manifest 记录的页 ID、版本和路径关联资料，直接沿用编码文件名。检索正文使用 `.md`，HTML 与其一同保留供核对。

`DHUB_CONFLUENCE_MANIFEST` 或 YAML 的 `confluence_manifest` 填**实际清单文件**。如果 `data/confluence/manifest` 就是清单文件，可以直接指定它。若 `manifest` 是子目录、`pages/` 与它同级，当前 `Knowledge` 加载器可使用放在 `data/confluence/manifest.json` 的兼容清单。加载器从清单所在目录解析 `path`，要求正文路径位于该目录内。已有导出记录应保留版本、标题、FD、父层级和源链接。

兼容清单的最小示例使用现有的页信息和编码文件名。

```json
{
  "documents": [
    {
      "page_id": "<实际页面 ID>",
      "version": "<实际版本>",
      "title": "<实际标题>",
      "feature_drop": "<对应 FD>",
      "path": "pages/<现有编码文件名>.md"
    }
  ]
}
```

从项目根运行下面的预检。确认 `document_count` 与应纳入范围的页版本数一致。`skipped_count` 是跳过的清单行数，`skipped_pages` 列出前 50 行的 `row`、`page_id`、`version` 和 `reason`，例如文件不存在、路径越出清单目录、非 UTF-8、重复页版本或缺少字段。清单本身无法读取时，`error` 说明是文件不存在、不是文件还是 JSON 无效。再选一个已知规则，验证 `search_documents` 的命中能用 `page_id`、`version` 和 `section_id` 读回对应原文。

```sh
python -c "from dhub.config import Settings; from dhub.sources.documents import Knowledge; print(Knowledge(Settings.from_env().manifest_path).snapshot())"
```

## 固定仓库并复用 CodeGraph

1. 在 `data/repositories.json` 登记 `decision-engine-instant-decision-svc` 的实际 Git checkout 路径和 `dev`，或本地可用的 `origin/dev`。已有单仓库配置可直接核对。在该业务仓库中执行 `git rev-parse --verify 'dev^{commit}'` 取得准备调查的 SHA，使用远程跟踪分支时将 `dev` 换成 `origin/dev`。
2. 从内网当前版 **MDC Assistant 复用 CodeGraph 工具函数和底层查询、索引读取实现**。以实际版本的源码确定依赖边界。DHub 项目适配器直接调用复用代码，工厂使用 `DHUB_CODEGRAPH_ADAPTER=module:factory` 或 YAML 的 `codegraph_adapter` 注册。 [适配契约](../dhub/sources/codegraph.py)
3. 实现 `index_info(repo_id, deadline=...)`，返回 `CodeGraphIndex`，提供索引实际对应的 `commit_sha`、稳定的 `index_id`、`provider` 和提供方已知的语言覆盖。SHA 应来自索引构建记录，并与第 1 步的 Git SHA 核对。
4. 实现 `explore(repo_id=..., index=..., query=..., deadline=...)`。将同一索引和剩余时间传给底层操作，返回带实际索引绑定的 `CodeGraphResult`。已有文本用 `content` 保留，结构化关系用 `results`，并传递实际 `status`、`truncated` 和 `warnings`。DHub 对同次运行的结果分页，且会验证图查询前后的索引身份。 [图工具和分页](expected-tools.md#34-codegraph_explore)

站点配置可存为 `data/dhub.yaml`，用 `DHUB_CONFIG_FILE=data/dhub.yaml` 加载。以下示例沿用内网已验证的 `DHUB_LLM_*` 或 `LLM_*` 设置。

```yaml
data_mode: project
confluence_manifest: data/confluence/manifest.json
repositories_file: data/repositories.json
codegraph_adapter: intranet_codegraph:create_adapter
```

每次分析或聊天都会调用一次工厂并读取索引信息。快照中的 `codegraph_reason` 区分以下情况，`codegraph_error` 给出简短原因，服务端日志保留完整异常。

| `codegraph_reason` | 含义与处理 |
| --- | --- |
| `codegraph_not_configured` | 未设置 `DHUB_CODEGRAPH_ADAPTER` |
| `codegraph_adapter_invalid` | 配置不是 `module:factory` 格式 |
| `codegraph_adapter_load_failed` | 无法导入模块或找不到工厂，检查 `PYTHONPATH`、模块名和依赖 |
| `codegraph_adapter_init_failed` | 工厂调用失败，检查索引路径、配置和凭证注入 |
| `codegraph_index_unavailable` | `index_info` 抛出异常 |
| `codegraph_unavailable` | `index_info` 返回空，适配器没有该仓库的索引 |
| `codegraph_invalid_index`、`codegraph_timeout` | 索引信息缺少 SHA 或 ID，或查询超时 |
| `codegraph_stale`（`codegraph_status` 为 `stale`） | 索引 SHA 与固定提交不同 |

如目标清单或适配器位置不同，填实际路径与工厂。同名变量以进程环境优先于 `.env`。应用配置优先读取这些环境变量，再读取站点 YAML 和内置 YAML。模型设置中 `DHUB_LLM_*` 优先于相应的 `LLM_*`。相对路径以 DHub 项目根为基准，仓库 JSON 内的路径以该 JSON 所在目录为基准。

## 跑一次完整 PoC 并记录结果

1. 在当前 Python 环境运行 `python -m pip check` 和 `python -m unittest discover -s tests -v`。启动 `python -m uvicorn dhub.app:app --host 127.0.0.1 --port 8000`，确认 `/api/health` 的 `data_mode` 为 `project`、`chat_mode` 为 `online`，然后从实际调查 Trace 核对内网 provider 的工具调用。
2. 用一个已知 Python 函数核对图的定义、已知调用方或调用链。查看本次 `source_snapshot` 中图和源码的 SHA、索引 ID、覆盖状态。用 `read_file` 核对图返回的关键调用位置。脚本和配置可用 Git 工具查证。
3. 在桌面页面用一条真实、经业务方或开发核对的需求运行 **Run Analysis**。选择同时涉及文档规则和代码调用关系的案例，例如 Mandatory Outbound Campaign 的频控豁免。核对 Domain 读到实际页版本，Code 调用真实 CodeGraph 并读到相同提交的源码，Main 引用对应 Evidence 形成报告。若文档与代码不同，报告应说明差异并引用两侧证据。
4. 保存 `run_id`、页版本、Git SHA、索引 ID 和已核实的结论。服务端控制台按 `DHUB_LOG_LEVEL`（默认 `INFO`）输出知识快照、仓库固定、CodeGraph 加载和索引查询、每次 LLM 调用的耗时，网关返回 usage 时附带 Token 数，运行结束时输出总耗时、调用次数和 Token 合计。刷新页面或调用 `GET /api/runs/{run_id}` 和 `GET /api/runs/{run_id}/events?after_seq=0` 核对回放。再测一次索引不可用或 SHA 不匹配，确认 Git 调查能继续且报告显示缺口。

### 区分失败与部分完成

- `partial`：Main 交付了报告，但调查受限，例如资料或索引不可用、工具失败、预算或时限用尽。缺口写在 `open_questions`。
- `failed`：Main 的模型调用失败（网关 HTTP 错误、连接失败、请求超时）或两次都没有返回符合结构的报告。运行的 `error` 带上网关返回的错误说明，API key 会被遮盖。失败前登记的证据仍保留在报告的 `evidence` 中。
- 工具失败在 Trace 行内显示错误码，展开可看原因。同样的 `error`、`message` 和恢复提示会返回给调查员，派单结果中的 `tool_errors` 让 Main 判断重试、改查或记录缺口。
- Git 失败保留 Git 自己的说明，例如 `Git said: fatal: detected dubious ownership`。服务账号与仓库属主不同时，按提示设置 `safe.directory`。

完成标志是一份使用**内网实际 Confluence、LLM、Git 提交和复用版 CodeGraph** 生成的可回放影响报告，且关键结论能定位到真实文档和源码。用 [PoC 验收](poc-acceptance.md#项目资料上的评估) 扩展真实案例覆盖。
