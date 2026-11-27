# DHub Impact Analyser

## 代码导航与目录约定

- 修改前读 [dhub/README.md](dhub/README.md) 和目标核心模块的 README。存在 `.codegraph/` 索引时优先用 CodeGraph 定位代码，建立新索引由用户决定。
- `dhub/` 顶层用于应用入口 `app.py`、分析入口 `analyzer.py` 和包初始化。功能实现按职责放入对应目录。
- `agents/` 管理 Agent、调查计划和共享运行时。`prompts/` 存放提示词，`reports/` 维护报告结构和证据引用校验。
- `sources/` 管理 Confluence、Markdown、固定 Git 提交和 CodeGraph。资料工具依赖来源后端。`sources/manager.py` 在单次运行中组装资料快照和工具，是两者的组合入口。
- 一个模型工具对应 `tools/<工具名>.py`。在所属工具集中注册，通用资料路径、读取和匹配逻辑放在 `sources/utils.py`，工具专用验证和包装放在 `tools/utils/`。
- 模型适配、应用配置和 SQLite 记录分别在 `llm/`、`config/` 和 `store/`。新增 provider 或配置时沿用现有注册和加载入口。
- 以当前代码、配置和测试核实现有行为。变更职责、入口或工具契约时，同步模块 README 与相关的 `docs/` 说明，保持内网交接可按当前路径执行。

## 前端范围

DHub Impact Analyser 只面向桌面浏览器。前端实现、视觉检查和验收均使用桌面浏览器尺寸。

## 验证

按 [tests/README.md](tests/README.md) 运行对应测试。重构共用入口后，核对工具接口、离线演示、模拟网关调用和回放。
