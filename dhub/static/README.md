# Static

桌面端页面由 [app.py](../app.py) 提供。分析页和聊天页共用计划、证据链接和 Trace 组件。

| 文件 | 职责 |
| --- | --- |
| [index.html](index.html) · [app.js](app.js) | 提交影响分析，显示运行进度、报告和证据，支持回放和 Report History |
| [chat.html](chat.html) · [chat.js](chat.js) | 多轮聊天、流式状态、浏览器本地会话列表，以及可点击的证据引用 |
| [investigation.js](investigation.js) · [styles.css](styles.css) | 共享的计划、Trace 展示、证据跳转和页面样式 |
| [chat-render/](chat-render/) · [vendor/](vendor/) | Markdown 和图的渲染代码、隔离的图框架及随项目提供的依赖 |

修改 UI 时按桌面浏览器尺寸检查。新增结果字段时从 API 数据和现有组件接入，检查实时显示与刷新回放。 [API 契约](../../docs/api-ui-trace.md)
