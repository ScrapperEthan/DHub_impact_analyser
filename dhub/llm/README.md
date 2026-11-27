# LLM

模型网关适配器使用统一的 `ChatClient.chat()` 接口，返回 `ChatResult`。AgentRuntime 处理会话、工具执行和剩余时间。

| 文件 | 职责 |
| --- | --- |
| [types.py](types.py) | 客户端接口、对话结果和 `LLMError` |
| [http.py](http.py) | 网关 URL 检查、HTTP 请求和通用错误处理。`LLMError` 带上 HTTP 状态、网关返回的错误说明或连接异常类型，并遮盖 API key |
| [openai_chat.py](openai_chat.py) | Chat Completions 请求及工具调用转换 |
| [local_responses.py](local_responses.py) | Responses 请求、工具调用及跨轮 reasoning 项 |
| [__init__.py](__init__.py) | `PROVIDERS` 和 `create_client()` |

站点参数统一由 [config/](../config/README.md) 读取。新增 provider 时在独立文件实现接口，在 `PROVIDERS` 注册，并用模拟网关验证请求与工具调用往返。合成演示的 `offline` 路径由 [AgentRuntime](../agents/runtime.py) 和各角色处理。
