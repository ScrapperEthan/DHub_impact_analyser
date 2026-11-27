# DHub Process Impact Analyser — Documentation

DHub（DecisionHub）在开发前分析 Business 或 Developer 提出的变更，定位相关流程步骤、规则、源码、配置和测试，并给出有来源的影响判断与回归场景。目标资料来自 Confluence FD1–FD5、Core Component Documentation 和一个 Git 仓库，仓库语言构成为 94% Python、4% Shell、2% PowerShell。代码调查使用 DHub 自身的 Git 工具；V0 内网联调目标是复用 MDC Assistant 的 CodeGraph 相关 Tools 及底层实现，不依赖 MDC Assistant 应用本身。内置演示使用合成资料。

## 从这里开始

| Document | Scope |
| --- | --- |
| [Current Status](current-status.md) | 本地实现、测试、内网联调进度与下一步；动态状态的统一入口 |
| [Expected Workflow](expected-workflow.md) | 三个 Agent 的目标流程、Decision Process Baseline、Frequency Cap 示例、当前 LangGraph 与目标的差距 |
| [Expected Tools](expected-tools.md) | Main Agent 规划和派单、十个资料工具、数据快照、V0 搜索与 CodeGraph、V1 Hybrid RAG 候选设计 |
| [Project README](../README.md) | 本地启动、模型配置、导入 Confluence、接入 Git 与 CodeGraph |
| [内网 PoC 交接](intranet-poc-handoff.md) | 现有 Markdown/HTML 导出、站点 YAML、复用 CodeGraph、端到端验收 |
| [DHub 源码导读](../dhub/README.md) | 核心模块职责、源码入口和依赖关系 |

## V0 PoC Specifications

| Document | Scope |
| --- | --- |
| [PoC Requirements](poc-requirements.md) | V0 产品需求、Decision Process Baseline、交付范围 |
| [Agent Spec](agent-spec.md) | 当前 V0 流程、与目标的交接差异、Agent 职责及工具 |
| [API, UI and Trace](api-ui-trace.md) | API、网页、报告与流式计划和 Trace 契约 |
| [PoC Acceptance](poc-acceptance.md) | 合成案例、异常场景、真实项目评估 |

## 版本路线图

V0 的验收范围以 [PoC Requirements](poc-requirements.md) 为准，当前完成情况见 [Current Status](current-status.md)。V1 和 V2+ 列出待评估的候选项目，实际投入取决于真实资料的联调与评估。

| 能力 | V0 PoC 当前实现与待验证项 | V1 候选 | V2+ 候选 |
| --- | --- | --- | --- |
| 业务知识 | 用现有 Confluence 导出验证原文导入、目录与关键词检索、版本化阅读和证据溯源。在 `data/knowledge/wiki/` 预留主题 Wiki 目录 | 完善基线、术语表和别名。评估 OKF Generate Skill 整理主题 Wiki，以及 Wiki 专用检索和 Hybrid RAG | 经治理的规则目录，以及在需要复用规则、流程步骤和实体关系时评估业务知识图谱 |
| 代码调查 | 单仓库固定 Git 提交、源码和配置及测试的只读检索；绑定真实 `decision-engine-instant-decision-svc` 的 `dev` 分支，复用 MDC Assistant 的 CodeGraph Tools 并验证索引与提交一致性 | 优化代码检索、共享调用识别和图覆盖 | 跨服务、配置及事件依赖识别，持续更新索引 |
| 影响分析 | 聊天和报告共用主 Agent 的自适应规划、多轮派单、历史发现和证据登记。支持可定位证据与报告内关联 | 加强任务与 findings 的证据关联，扩展历史需求 Golden Set，提升结论和证据质量 | 持久化规则、流程步骤、代码、配置与测试之间的关系，按需增加独立的 persona/simulation 验证 |
| 产品交付 | 桌面 Web UI 流式显示调查步骤和计划，SQLite 支持分析回放；内网联调、真实需求 PoC 验收与基线评估 | 扩展 BA/Developer 评估范围、授权和数据治理 | 持续更新知识，支持团队复用与流程集成 |

### 知识库分期

V0 从 `data/confluence/` 载入带清单的版本化 Markdown，验收原文导入、章节检索、精确读取和证据溯源。主题 Wiki 在 `data/knowledge/wiki/` 预留目录，按主题目录与 `topics/` 下的业务页组织。`data/` 不纳入 Git，需要时在内网创建该目录。

后续可用 **OKF Generate Skill** 辅助 OpenCode 整理主题页，并依据实际查询场景评估专用检索。Wiki 是导航与归纳用的知识增强层，每条业务规则须能追溯到原始 Confluence 的页 ID、版本和章节。内容生成与专用检索可随真实案例验证逐步推进。 [目录设计](intranet-poc-handoff.md#主题-wiki-目录设计)

### 联调与评估顺序

1. **验证真实 V0。** 复用内网的 Confluence 导出和模型网关，在 `project` 模式绑定真实仓库与 CodeGraph。核对 FD、章节、源码和图索引覆盖，用真实需求验证工具调用和证据链。 [内网步骤](intranet-poc-handoff.md)
2. **建立 Golden Set。** 选择 BA 与 Developer 已核对的历史变更，标注规则、例外、Step、源码、依赖和回归场景，记录文档和代码版本，优先采用开发前的快照。
3. **测量 V0 基线。** 统计规则与例外召回、代码影响准确率与召回率、引用正确性、冲突检出、缺少支持的确认项、工具次数和耗时。按检索、工具覆盖和 Main 综合定位漏检或误判。
4. **用同一批案例验证检索升级。** 评估章节级 BM25/FTS 与向量召回、结果融合及原文追溯，同时沿用现有工具接口。
5. **加强任务和证据契约。** 为需求、任务和两侧 findings 建立结构化类型，校验结论与原文是否对齐。
6. **按复用需求持久化关联。** 先用关联表或索引复用已核实的规则、步骤、代码、配置与测试关系。关系复杂度和重复调查量足以支撑投入时，再评估业务知识图谱。CodeGraph 继续用于代码静态依赖。

V0 真实需求验收应能在代表性变更上找到正确的规则和实现，列清直接修改与仅需验证的范围，并用原文说明理由。V1 在同一基线之上验证质量和效率改进。当前单工作线程且无账号隔离，适合受控 PoC。进入更广泛的部署前需验证授权、数据隔离、审计和容量。 [项目资料评估](poc-acceptance.md#项目资料上的评估)

## 源码导读

| 顺序 | 入口 | 看什么 |
| --- | --- | --- |
| 1 | [analyzer.py](../dhub/analyzer.py) | 聊天、影响分析、快照和截止时间入口 |
| 2 | [main.py](../dhub/agents/main.py) · [planning.py](../dhub/agents/planning.py) · [domain.py](../dhub/agents/domain.py) · [code.py](../dhub/agents/code.py) | 主 Agent 的 LangGraph 循环、计划校验、调查交接和离线示例 |
| 3 | [runtime.py](../dhub/agents/runtime.py) · [prompts/](../dhub/prompts/README.md) | 原生工具调用、预算、Trace、角色提示词和六阶段基线 |
| 4 | [sources/](../dhub/sources/README.md) · [tools/](../dhub/tools/README.md) | 如何绑定资料版本、查规则和代码、生成原文证据 |
| 5 | [reports/](../dhub/reports/README.md) · [app.py](../dhub/app.py) · [store/](../dhub/store/README.md) · [static/](../dhub/static/README.md) | 报告校验、API、存储与页面回放 |
| 6 | [llm/](../dhub/llm/README.md) · [config/](../dhub/config/README.md) · [demo](../demo/) · [tests/](../tests/README.md) | 模型适配和设置、合成资料、离线与模拟网关用例 |

依照项目 README 启动或运行测试后，在本地网页填入示例。从 Trace 跟到代码补查，再从报告里的证据跳回实际工具调用。 [快速启动](../README.md#快速启动) · [本地烟测](../README.md#内网验证与排查)
