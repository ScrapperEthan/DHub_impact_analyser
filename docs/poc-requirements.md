# DHub Process Impact Analyser V0 PoC 需求

2026-11-25 · 需求草案

Business 或 Developer 输入一条拟议变更，系统调查它会影响 DHub 的哪些 Decision Process 步骤、业务规则、代码、配置和测试，并说明影响如何传递、证据在哪里、还有哪些问题需要确认。V0 的交付物是一个能从网页提交需求、查看调查 Trace、读取影响报告的前后端 PoC。

## 用户与使用流程

- **Business** 输入业务目标或 User Story，重点查看影响哪些决策步骤、会改变什么行为，以及新需求与已有规则的冲突。
- **Developer** 输入业务或技术变更，重点查看受影响的仓库、函数、调用方、配置和测试，并打开对应证据。

输入是一段中英文自然语言。用户可以在原文中写明 Campaign、Channel、Market、Inbound 或 Outbound、Batch 或 Real-time、边界条件及期望行为。若适用范围会改变结论，报告应列出假设或待确认问题。能在已知范围内开展的调查应继续。

常规流程为 **提交需求 → 业务调查 → 代码调查 → 核对并生成报告**。运行中可以查看三个 Agent 的进度和实际工具调用。运行完成后，从报告中的证据可以跳到对应的 Trace 记录。

## 三个 Agent 的产品职责

| Agent | 要回答的问题 | 交付给谁 |
| --- | --- | --- |
| **Main Process Impact Analyser** | 新需求想改变什么？在业务规则、决策步骤和技术实现之间，哪些影响有证据，哪些需要验证？ | 向两个 Investigator 派发调查任务，向用户交付最终报告 |
| **Domain Knowledge Investigator** | Confluence 在什么范围、什么版本中定义了哪些规则、例外和决策步骤？后续 Feature Drop 有无相关变更或不同表述？ | 向主 Agent 交付业务发现、原文证据、代码搜索线索和知识缺口 |
| **Code Investigator** | 两个 Python 仓库的指定提交如何实现这些规则？涉及哪些调用方、共享逻辑、配置和现有测试？ | 向主 Agent 交付实现发现、代码证据、依赖关系和待验证项 |

主 Agent 持有精简的 Decision Process Baseline、系统概览和术语表。两位 Investigator 可以在同一次运行内多次搜索和阅读，主 Agent 根据证据缺口安排一轮定向补查。三位 Agent 可以随同一个后端进程运行，分别拥有任务、输出和 Trace 身份。[Agent 职责、工具和知识准备](agent-spec.md)

## DHub 资料与决策基线

DHub 即 DecisionHub，已有资料描述了一套从候选 Campaign 到选定推送结果的 Decision Process。可用资料是 Confluence 和两个 Python 仓库。Confluence 包含 FD1–FD5（Feature Drop 1–5）及 Core Component Documentation 等页面，项目已能通过 Confluence MCP 获取页面、版本、更新时间、更新人、Ancestors 和附件信息，并转换为 Markdown。仓库内可调查源码和存在的配置、接口契约及测试。两仓库的职责、提交和工具接入方式在项目联调时确认。

以下是从 FD2 Requirements 下的 **Decisioning Process** 页面起步的 Decision Process Baseline。步骤号沿用该页的 21 步编号，可作为初始导航。规则是否适用、代码是否实现以及部署状态，以各自来源和调查范围为准。

| 阶段 | FD2 步骤 | 初始调查范围 |
| --- | --- | --- |
| P1 Initialization | 1–3 | 取得客户和渠道上下文，加载并按渠道筛选活跃 Campaign |
| P2 Multi-Layered Eligibility Filtering | 4–8 | Campaign、Channel、Communication Strategy 的资格条件，以及实时上下文和客户旅程 |
| P3 Business Value Calculation | 9 | 计算候选 Campaign 的 Arbitration Score |
| P4 Contact Permissions | 10–13 | Contact Policy 和 Suppression Rules |
| P5 Final Arbitration & Fatigue Management | 14–20 | UCG、Gradual Rollout、按分数排序、冲突解决、Frequency Cap、Quiet Time、Channel Limit 和 Mobile Slot Suppression |
| P6 Delivery | 21 | 推送选定的 Campaign |

其中 FD2 在 P5 Step 18 描述了 Frequency Cap 及 Mandatory Campaign 的配置豁免。P3 的分数计算与 P5 的排序是两个调查位置。CEH Eligibility 可从历史互动相关的资格判定着手调查，其实际接入位置和实现状态由证据确认。来源为 Decisioning Process 页面、流程图和 Confluence 目录。

## V0 交付范围

| 必须能做 | V0 产品要求 |
| --- | --- |
| 提交和运行 | 提供一个网页和分析 API，接受一条变更需求，给出运行状态并显示结果 |
| 调查业务 | 导入一组可用于样例的 Markdown 原文与元数据，按章节搜索和阅读，按 FD 或页面层级导航，找到规则、例外和依据 |
| 调查代码 | 支持在两个配置仓库中只读搜索、带行号读文件、按可用的 CodeGraph 追踪调用关系，并记录仓库提交 |
| 综合影响 | 评估现有能力是否已支持需求，定位业务和流程影响，调查代码、配置及已有测试，提示规则冲突和可能的下游行为变化 |
| 用证据交付 | 展示结构化报告、证据、覆盖范围、风险及回归建议。每条已确认影响能定位到本次运行读取的来源 |
| 可见的工具调用 | 页面显示三个 Agent、每次真实工具调用的状态、参数摘要、结果摘要和用时。刷新同一运行能回看 Trace |
| 资料缺失时运行 | 有可用结果时交付部分报告，明确标出资料缺失、工具超时或覆盖不足。演示语料有独立的运行标记 |

**V0 的目标是开发前的只读影响分析。** 输出中的预期行为来自需求与证据推断。完整 Topic Wiki、关键词加向量的混合检索、持久化业务技术关联、Domain Knowledge Graph、跨服务依赖自动识别及 Synthetic Persona 前后对比属于后续候选。混合检索是 V1 的首个检索升级候选，由真实样例暴露的漏检决定投入优先级。

## 本轮实现选择

- **知识检索**使用带来源元数据的 Markdown、轻量目录和关键词检索。目录和少量术语别名提供导航，Investigator 通过多轮检索和原文阅读补齐证据。V1 可沿用同一工具接口增加向量检索。
- **分析后端**建议 Python、FastAPI、LangChain 工具和 LangGraph 状态图。用一个应用承载三个角色与有限的补查循环。LangGraph 支持用共享状态和条件边表达调查顺序。具体模型与接入地址从运行环境配置。[LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)
- **前端**采用单页 UI，显示需求输入、报告和 Trace。PoC 用轮询刷新事件，SQLite 保存运行结果及 Trace，便于本地启动和回放。[API、页面和 Trace](api-ui-trace.md)

## 联调输入

| 输入 | 用途 |
| --- | --- |
| 两个仓库的访问方式、职责、分支和可固定的提交 | 核实真实的模块分工和技术影响 |
| Confluence MCP 导出方式及获准使用的页面范围 | 建立带页 ID、版本和 Section 的项目知识快照 |
| MDC Assistant 四个代码工具的接口与 CodeGraph 索引信息 | 验证可复用能力和真实索引覆盖，确认分页契约 |
| 模型端点与项目可使用的模型 | 在授权环境中执行实际工具调用分析 |
| 至少一条经 BA 或 Developer 核对的历史需求及其影响 | 评估真实业务召回和引用准确性 |

演示可先使用可辨识的合成语料，并从同一 UI 跑通三位 Agent。数据和工具接入可用后，再按同一契约切换到项目语料。[演示场景与验收](poc-acceptance.md)
