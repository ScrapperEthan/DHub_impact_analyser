# DHub PoC Agent 职责和工具

在线聊天和影响分析共用 Main Agent 和 LangGraph 调度。两个 Investigator 各有模型上下文、只读工具白名单和 Trace 身份，工具执行与证据登记由 `AgentRuntime` 负责。 [Expected Workflow](expected-workflow.md) · [Expected Tools](expected-tools.md)

## 当前 V0 运行流程

`analyzer` 绑定文档和仓库快照，然后由 `MainProcessImpactAnalyser` 启动在线循环。聊天用会话历史回答当前问题，影响分析按证据覆盖要求调查变更。 [运行入口](../dhub/analyzer.py) · [主 Agent](../dhub/agents/main.py)

![当前 V0 LangGraph。主 Agent 选择业务或代码调查，读取返回结果后继续判断，或校验并交付最终回复。](diagrams/current-workflow.svg)

### Adaptive Planning

每次请求从 `plan = null` 开始。Main 可以直接回答、用上下文决定下一次调查，或通过 `update_plan` 发布结构化计划。后续发现可以触发首次发布，也可以调整范围、候选流程步骤和任务状态。每个有效版本包含目标、范围、任务及调整原因，并作为 `plan.updated` 事件发送到页面。计划操作和调查派单都使用现有 LangGraph 的 `main → delegate → main` 循环。 [计划结构](expected-tools.md#12-update_plan) · [事件契约](api-ui-trace.md#调查计划事件)

Main 从工具结果读取最新版本。对于仍有待调查事项的终稿，Main 可再判断一次是否补查或记录原因。最终仍开放的任务会标为 `blocked`，缺口进入分析报告的 `open_questions` 或聊天回复。明确跳过的任务保留公开说明。分析的完成标准仍以证据和覆盖要求为准。

### 多轮调查和交接

`main` 在 `question` 中说明范围、已知发现和待核实问题，并在显式计划中附上 `task_id`。`delegate` 执行派单，每个 Investigator 都收到原始需求、当前计划（如有）、快照和两侧已有 findings。Trace 记录派单问题和适用的任务 ID。运行时将源文档或代码的读取证据绑定到对应任务，并在计划快照中发布 `evidence_ids`。 [共享规划指令](../dhub/prompts/main-plan.md)

调查结果带着本次新登记的证据返回 `main`。例如，Main 可根据频控规则写出代码任务，调查 `should_allow_campaign` 的 Outbound 分支和 Inbound 调用。发现适用范围仍有缺口时，再派 Domain 核对例外。两种任务模式都可以重复派单。

报告终稿由 Pydantic 校验。模型有一次格式修复机会，`ground_report` 校验本次运行的证据 ID 并降级缺证结论。引用原文与结论的语义对齐仍需核对。离线 Frequency Cap 样例按脚本检查原文、代码和补充资料。

### 运行状态和预算

单次分析或聊天请求最多执行 **50 次资料工具调用**。主 Agent 的派单单独计数，上限 50 次。每位 Investigator 单次受派最多执行 8 次资料工具调用。`update_plan` 单独计数，每次运行最多 50 次尝试。运行时限从快照准备开始计算，最高 **300 秒**，`DHUB_RUN_TIMEOUT` 可设置更短时限。到时结束运行，尚未返回的模型或资料结果不会写入证据和 Trace。若已取得部分证据，分析会交付带限制说明的报告。 [运行时源码](../dhub/agent_runtime.py)

## Main Process Impact Analyser

**目标输入**为用户需求、资料快照和常驻背景。常驻背景包含 Decision Process Baseline、DHub 系统概览、核心术语和分析约束。Baseline 中的阶段、步骤和已知规则保留来源定位。当前在线角色共用含六阶段精简基线的提示词。 [当前共用提示词](../dhub/prompts/common.md)

**目标职责**

1. 保留用户原文，将意图、变更类型、作用对象、渠道或模式、已知条件和假设写入规范化需求。可识别规则新增、修改、删除、配置变更或流程插入点等。
2. 把可能相关的 Process Step 和要查的问题交给 Domain Knowledge Investigator。根据其规则、例外、术语和文档差异，给 Code Investigator 明确的验证任务。
3. 综合两侧调查，说明现有能力对需求的支持情况。对文档所述、代码中观察到的分支和测试断言分别保留来源。若文档和代码对于同一范围描述不同，保留两边证据与适用范围。
4. 在本次运行内建立影响链。例如以 `Outbound Mandatory Campaign 豁免` 出发，找到 P5 Frequency Cap，再核对实现分支、调用方、配置及对应断言。对每一段说明关系、影响原因和证据。上下游顺序用于定位调查，依赖证据用于判断影响。共享调用方可触发隔离验证，下游候选集合变化可触发行为风险，代码修改需求由具体实现证据支持。
5. 发布结构化报告。对关键项校验 `evidence_ids` 来自本次运行已读取的快照并与结论对应。证据不足时降低结论确定性并注明缺什么。对输入太笼统且无法形成调查任务的情况，返回待补充信息。

## Domain Knowledge Investigator

**目标输入**为主 Agent 的业务问题、适用范围和术语，以及运行绑定的 Confluence 快照。它可以使用精简 Baseline 和术语别名来组织调查。V0 在线每次接收原始需求、派单、已有发现和快照。

**调查流程**

1. 分解业务对象、规则别名、例外、渠道和配置，例如分别查 `frequency cap`、`mandate campaign override` 和 `outbound batch`。
2. 用目录、关键词和元数据过滤定位章节，打开完整小节或父文档，核对适用条件、表格、相邻例外和附件线索。
3. 对高影响规则检查相关后续 FD 和引用页。记录同一范围下的差异、明确的覆盖说明，以及仍有疑问的版本。页面版本号、FD 归类与部署状态各有独立语义。按日期或 FD 序号只能找候选文档，覆盖关系需要原文证据。
4. 将业务发现、原文引用、检索范围和代码调查线索交回主 Agent。只有原文可支持的规则陈述才作为已核对的文档事实。计划中的设计保留 `proposed` 状态，实施情况交给代码调查。

**目标输出 `DomainFindings`**

- `rules[]` 包含业务规则、适用范围、例外、涉及的 Phase 和 Step、文档状态及 `evidence_ids`。文档状态可为 `documented`、`proposed`、`superseded` 或 `unknown`。
- `conflicts[]` 包含相关规则或提案、重叠范围、差异和证据。
- `code_hints` 提供 `business_terms`、`possible_identifiers`、`config_keys`、`suspected_modules` 和来源 `evidence_ids`。候选命名由 Code 在固定提交中验证。
- `coverage` 和 `open_questions[]` 记录调查看过的 FD 或页面、资料缺口及需代码验证的问题。

### 知识准备与检索

在分析运行外导入经授权的 Confluence MCP 导出。开发态可以加载合成文件。按快照保存原文 Markdown 和轻量文档目录，使用 `page_id` 加 `version` 标识一份原文。页版本或附件不可取得时在覆盖范围中记录。

文档目录保留标题、Page ID、版本、源 URL、更新时间、更新人（如获准提供）、Ancestors、Feature Drop、章节路径、页面链接和附件元数据。Feature Drop 可由层级派生，主题和摘要可少量人工添加。解析时尽量保留标题层级、表头、接受标准与条件分支。用小节做检索单位，按需读取相邻章节、整页和可用附件。目录摘要和命中片段用于导航，报告证据定位到原文。

V0 加载快照时预计算各章节的规范化正文和关键词。检索时根据完整短语、标题与查询词覆盖排序。BM25 或 FTS 与章节向量融合是 V1 候选，沿用 `search_documents` 的结果格式。 [当前打分与工具契约](expected-tools.md#22-search_documents) · [V1 检索方案](expected-tools.md#25-v1-候选--hybrid-rag)

| 工具 | V0 输入及返回 |
| --- | --- |
| `search_documents` | 按关键词搜索，可过滤 FD、Ancestor、页 ID 和版本。返回章节 ID、行号、片段和覆盖状态 |
| `read_document` | 用页 ID、版本和章节 ID 读取原文，支持按字符偏移续读 |
| `list_documents` | 可按 FD 或 Ancestor 浏览，按 `next_offset` 遍历目录 |
| `get_document_metadata` | 以 Page ID 和版本返回元数据。若 `read_document` 已返回完整元数据，本次可省略调用 |

沿链接和同级页面探索可先复用目录元数据与上述工具。引用的附件未导入时要记录这个缺口，避免把只读到的页面视为附件内容已核实。

## Code Investigator

**目标输入**为代码调查任务、业务线索和项目仓库的固定快照。任务中应明确要查的现有条件、调用方、配置范围和已有测试。V0 交接提供原始需求、已有 Domain findings 和当前显式计划（如有）。

**调查流程**

1. 获取仓库的身份、分支及提交。索引与提交匹配时，可用 CodeGraph 查找已知符号的实现和调用关系。通过代码或配置名的变体搜索定位其他候选。
2. 阅读关键源文件的带行号上下文，核对真实分支、配置读取、默认值、调用侧限定和测试断言。注释、名称匹配和测试用例的意图分别说明。
3. 利用可用的 CodeGraph 调查 caller、callee、调用链及共享实现，必要时按窗口继续读。核对关键边对应的调用位置。跨 Repo 关系需要可引用的调用、共享契约或配置证据。静态图缺边、动态分派、外部配置和代码快照缺失写入调查限制。
4. 返回提交级实现证据。将现有测试与建议新增测试分别交付，测试断言表示该提交可观察的预期。超过工具覆盖范围的项写入待确认清单。

**目标输出 `CodeFindings`**

- `implementations[]` 记录仓库、符号、观察到的逻辑、适用条件、`evidence_ids`。
- `dependencies[]` 记录调用方和被调用方或共享依赖、关系类型、适用范围和 `evidence_ids`。
- `configurations[]` 和 `existing_tests[]` 记录已读配置使用点、已有测试及其断言，以 `evidence_ids` 定位。
- `coverage` 和 `open_questions[]` 记录实际调查的仓库和提交、代码图可用性和需补证事项。

### MDC Assistant 工具复用

复用 MDC Assistant 的 CodeGraph 工具实现和底层查询、索引读取模块，由 DHub 适配层补上固定提交、索引身份、分页和 Trace。实际模块边界与返回值按内网源码核对。

| 工具 | V0 输入及返回 |
| --- | --- |
| `get_repository_metadata` | 以 `repo_id` 取得仓库身份、分支、commit SHA 和可用索引对应的提交。可由本地 Git 或仓库适配器提供 |
| `search_code` | 字面或标识符变体搜索。按仓库、路径前缀、glob 和扩展名过滤，返回命中词、原文位置及搜索状态 |
| `list_files` | 按目录和 glob 浏览固定提交的可读文件名，使用 `next_offset` 翻页 |
| `read_file` | 指定仓库、相对路径、行号和字符偏移，读取固定提交中的原文，支持长行续读 |
| `codegraph_explore` | 指定仓库和调查目标，返回文字或关系线索、索引身份、覆盖状态及续读句柄 |
| `codegraph_read_result` | 按 `result_id` 和 `next_offset` 读取同次运行捕获的内容，继承原索引和截断状态 |

在同一运行中锁定仓库提交与知识快照。CodeGraph 不可用或索引提交不匹配时，转用搜索和源文件核对并标明图覆盖缺失。工具只能访问已配置的语料和仓库，服务端负责校验路径、返回限制和超时。Demo 模式使用合成资料上的工具执行；项目模式依据实际接入情况报告。

## 调查和证据的关系

在线分析时建立一次运行内的业务到技术关联并保存在报告中。`read_document` 和 `read_file` 的原文证据带有本次运行的证据 ID 和工具调用 ID。比如文档定义豁免条件，代码读取确认配置和分支，测试阅读确认已有断言，从而支持主 Agent 解释为什么直改共享函数可能影响 Inbound。当前报告校验会剔除未知引用、降级缺证的已确认结论。结论是否得到引用原文支持仍需核对。 [报告与 Trace 契约](api-ui-trace.md) · [报告校验源码](../dhub/report.py)

Main 用文档确认规则描述和设计意图。文档可能过期，当前实现以实际核对的固定提交源码、配置和调用条件为准。发现冲突时引用双方来源并说明差异。摘要缺少关键条件时，Main 派发定向补读，再决定结论是否可以确认。
