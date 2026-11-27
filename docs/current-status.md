# DHub Process Impact Analyser — Current Status

- 更新日期：2026-11-27
- 当前阶段：V0 PoC / 内网联调
- 最近本地验证的代码提交：`a5f6ade`（34 个单元测试全部通过）

本文是项目动态进度的统一入口。区分本地已实现、自动化测试通过、内网已验证和待确认。需求范围、接口契约和未来路线图分别见 [PoC Requirements](poc-requirements.md)、[Expected Tools](expected-tools.md) 和 [Roadmap](README.md#版本路线图)。

## 1. 当前实现（本地代码）

| 能力 | 状态与说明 |
| --- | --- |
| Main Agent / Adaptive Planning | 已实现。在线 Chat 与 Analyser 共用 LangGraph 调查循环和运行时；支持直接答复、隐式调查、显式计划及 Replan |
| Domain Knowledge Investigator | 已实现。版本化 Markdown、目录及章节关键词检索、原文读取和证据定位；4 个资料工具 |
| Code Investigator | 已实现。固定 Git 提交、搜索、文件读取与 CodeGraph 适配契约；6 个资料工具 |
| Plan–Task–Evidence | 已实现最小关联：任务 ID、证据 ID、计划版本及 Trace；尚未完整校验 findings 与结论的语义一致性 |
| CodeGraph | 本地适配器契约、索引身份与 SHA 校验、分页和 Git fallback 已实现；演示使用合成图 |
| 业务背景 | 共享 Decision Process Baseline、系统概览及独立 Glossary 已实现 |
| 报告与可观测性 | 结构校验、证据引用校验、覆盖限制、工具失败原因、Trace、耗时和可用的 Token usage |
| Web UI 与存储 | 桌面端分析和聊天、流式调查与计划、SQLite 报告历史及回放、浏览器本地聊天会话 |
| 模型适配 | offline、Responses、Chat Completions 适配器；内网 provider 以实际环境为准 |
| 自动化测试 | 2026-11-27 在本地执行 `python -m unittest discover -s tests -q`，34/34 通过；覆盖演示、临时 Git 仓库和模拟网关，不等同于真实资料验收 |

## 2. 内网联调状态

以下内网已完成项来自项目开发者的进度确认；本地检查未直接访问内网环境，也未核实内网运行 ID 或部署 commit。

| 事项 | 状态 | 说明 |
| --- | --- | --- |
| Confluence 导出及接入 | 已接通 | 沿用已有 Markdown/HTML 和 manifest；完整页版本、章节覆盖仍需核对 |
| 内网 LLM 网关 | 已接通 | 沿用已可用的 provider、配置及凭证注入，不重复实现 |
| Impact Report 生成 | 已跑通 | 较早提交已在内网成功生成报告；尚不代表真实文档、代码及图证据链全部验收通过 |
| 最新本地代码同步内网 | 待确认 | 核对本地目录重构、工具契约、内网扩展的合并及目标环境测试 |
| 真实 Git 仓库绑定 | 待确认 / 验证 | 核对 `decision-engine-instant-decision-svc` 的 `dev`（或 `origin/dev`）、实际路径、固定 SHA 和原文读取 |
| 真实 CodeGraph 复用 | 待确认 / 验证 | 复用 MDC Assistant 的 CodeGraph 相关 Tools 和底层查询、索引读取实现，由 DHub 自己调用；不接入或依赖 MDC Assistant 应用本身 |
| 完整证据链及真实需求质量 | 待验收 | 核对文档页版本、源码行号、图索引身份、影响结论和可回放 Trace |

## 3. 下一步（V0）

1. 同步最新本地提交与内网已有扩展，保留已经可用的 Confluence 和 LLM 接入；运行依赖检查及测试。
2. 核实真实 Git checkout、固定 SHA、搜索与原文读取；如已接好，仅核对而不重复实现。
3. 复用 MDC Assistant 的 CodeGraph Tools 及必要底层模块，按 DHub 的适配契约绑定真实索引；验证调用关系、SHA、分页及图不可用时的 Git fallback。
4. 选取至少一条经 BA 或 Developer 核对的真实需求，运行 Analyser，检查文档、图、源码证据和报告回放；再按实际案例扩展 V0 基线评估。

操作步骤见 [内网交接](intranet-poc-handoff.md)，验收标准见 [PoC Acceptance](poc-acceptance.md)。

## 4. 已知限制与后续候选

- 当前是只读、单仓库、桌面端 PoC，不自动修改代码，不执行生产决策；无账号隔离，分析使用单工作线程。
- 当前知识检索为目录和章节关键词检索；`data/knowledge/wiki/` 是预留目录，代码尚不读取 Topic Wiki。
- CodeGraph 的实际语言、索引和依赖覆盖取决于内网复用实现；源码只能证明指定提交的行为，不能证明生产部署状态。
- 报告校验主要保证证据引用可定位，不能自动保证证据语义完全支持结论。
- V1 候选：Topic Wiki、Hybrid RAG、检索和图覆盖优化、结构化 findings 与更强证据校验。业务知识图谱及跨服务关系留待后续评估，不作为 V0 交付前置条件。

## 5. 维护约定

每次功能实现、测试结果或内网联调状态变化后更新本文；记录日期、实际验证的 commit、环境和结果。没有直接验证的事项保留为待确认，避免把本地测试通过或报告生成成功等同于内网完整验收。其他文档描述稳定职责、契约、操作步骤和目标范围，动态进度链接到本文。不要在此存放凭证、内部原文或敏感运行数据。
