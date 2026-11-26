# DHub Process Impact Analyser — Documentation

DHub（DecisionHub）在开发前分析 Business 或 Developer 提出的变更，定位相关流程步骤、规则、源码、配置和测试，并给出有来源的影响判断与回归场景。目标资料来自 Confluence FD1–FD5、Core Component Documentation 和一个 Git 仓库，仓库语言构成为 94% Python、4% Shell、2% PowerShell。代码调查可复用 MDC Assistant 的四个工具。内置演示使用合成资料。

## 从这里开始

| Document | Scope |
| --- | --- |
| [Expected Workflow](expected-workflow.md) | 三个 Agent 的目标流程、Decision Process Baseline、Frequency Cap 示例、当前 LangGraph 与目标的差距 |
| [Expected Tools](expected-tools.md) | 九个只读工具的契约、数据快照、V0 搜索与 CodeGraph、V1 Hybrid RAG 候选设计 |
| [Project README](../README.md) | 本地启动、模型配置、导入 Confluence、接入 Git 与 CodeGraph |

## V0 PoC Specifications

| Document | Scope |
| --- | --- |
| [PoC Requirements](poc-requirements.md) | V0 产品需求、Decision Process Baseline、交付范围 |
| [Agent Spec](agent-spec.md) | 当前 V0 流程、与目标的交接差异、Agent 职责及工具 |
| [API, UI and Trace](api-ui-trace.md) | API、网页、报告与 Trace 契约 |
| [PoC Acceptance](poc-acceptance.md) | 合成案例、异常场景、真实项目评估 |

## 版本路线图

V0 的验收范围以 [PoC Requirements](poc-requirements.md) 为准。V1 和 V2+ 列出待评估的候选项目，实际投入取决于真实资料的联调与评估。

| 能力 | V0 PoC 当前实现与待验证项 | V1 候选 | V2+ 候选 |
| --- | --- | --- | --- |
| 业务知识 | 已支持 Markdown、manifest、目录与关键词检索、版本化原文。内网 Confluence provider 待接入 | 完善基线、术语表和别名，增加 Hybrid RAG 与可选的轻量 Topic Wiki | 经治理的规则目录，以及在需要复用规则、流程步骤和实体关系时评估业务知识图谱 |
| 代码调查 | 已支持本地 Git 固定提交、只读搜索和原文阅读。CodeGraph 有适配接口和合成数据。单仓库覆盖判断及 Shell、PowerShell 文件支持待补齐 | 联调真实代码、配置、测试、共享调用及 MDC 索引 | 跨服务、配置及事件依赖识别，持续更新索引 |
| 影响分析 | 三个 Agent 已串起调查、综合、一次补查和报告，支持可定位证据与报告内关联。独立二次派单、强类型交接和证据语义校验仍待补齐 | 结构化需求与任务，保留补查上下文，用历史需求 Golden Set 验证结论和证据 | 持久化规则、流程步骤、代码、配置与测试之间的关系，按需增加独立的 persona/simulation 验证 |
| 产品交付 | 已有 Web UI、API、Trace、SQLite 回放和合成演示，已有测试覆盖离线与模拟模型网关。真实项目质量评估与部署验证待完成 | 内网联调、BA/Developer 验收、授权和数据治理 | 持续更新知识，支持团队复用与流程集成 |

### 联调与评估顺序

1. **验证真实 V0。** 用 `demo + openai_chat` 验证网关的工具调用协议，再切换到 `project + openai_chat`。核对导出的 FD、章节和链接，固定项目仓库的 ref，检查 CodeGraph 索引和脚本文件的覆盖。离线固定样例使用合成页和仓库 ID，真实项目验证需要模型网关。
2. **建立 Golden Set。** 选择 BA 与 Developer 已核对的历史变更，标注规则、例外、Step、源码、依赖和回归场景，记录文档和代码版本，优先采用开发前的快照。
3. **测量 V0 基线。** 统计规则与例外召回、代码影响准确率与召回率、引用正确性、冲突检出、缺少支持的确认项、工具次数和耗时。按检索、工具覆盖和 Main 综合定位漏检或误判。
4. **用同一批案例验证检索升级。** 评估章节级 BM25/FTS 与向量召回、结果融合及原文追溯，同时沿用现有工具接口。
5. **加强任务和证据契约。** 评估独立的代码任务细化，为需求、任务和两侧 findings 建立结构化类型，向补查传递历史发现、证据和缺口，校验结论与原文是否对齐。
6. **按复用需求持久化关联。** 先用关联表或索引复用已核实的规则、步骤、代码、配置与测试关系。关系复杂度和重复调查量足以支撑投入时，再评估业务知识图谱。CodeGraph 继续用于代码静态依赖。

V1 验收应能在真实变更上找到正确的规则和实现，列清直接修改与仅需验证的范围，并用原文说明理由。当前单工作线程且无账号隔离，适合受控 PoC。进入更广泛的部署前需验证授权、数据隔离、审计和容量。 [项目资料评估](poc-acceptance.md#项目资料上的评估)

## 源码导读

| 顺序 | 入口 | 看什么 |
| --- | --- | --- |
| 1 | [analyzer.py](../dhub/analyzer.py) | LangGraph 节点、状态流和补查条件边 |
| 2 | [main.py](../dhub/agents/main.py) · [domain.py](../dhub/agents/domain.py) · [code.py](../dhub/agents/code.py) | 三个角色如何传递发现，离线示例如何运行 |
| 3 | [agent_runtime.py](../dhub/agent_runtime.py) · [prompts](../dhub/prompts/) | 原生工具调用、预算、Trace、角色提示词和六阶段基线 |
| 4 | [sources.py](../dhub/sources.py) · [工具源码索引](expected-tools.md#7-source-code-mapping) | 如何绑定资料版本、查规则和代码、生成原文证据 |
| 5 | [report.py](../dhub/report.py) · [app.py](../dhub/app.py) · [store.py](../dhub/store.py) · [static](../dhub/static/) | 报告校验、API、存储与页面回放 |
| 6 | [demo](../demo/) · [test_poc.py](../tests/test_poc.py) | 合成资料、离线与模拟网关用例 |

依照项目 README 启动或运行测试后，在本地网页填入示例。从 Trace 跟到代码补查，再从报告里的证据跳回实际工具调用。 [快速启动](../README.md#快速启动) · [本地烟测](../README.md#内网验证与排查)
