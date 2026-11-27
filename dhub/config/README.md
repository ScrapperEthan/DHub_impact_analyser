# Config

[settings.py](settings.py) 的 `Settings.from_env()` 是应用配置入口。[settings.yaml](settings.yaml) 提供默认值，[model.py](model.py) 定义 `LLMConfig` 和 Responses 的默认本机地址。

`DHUB_CONFIG_FILE` 可指定站点 YAML。同名进程变量优先于 `.env`，环境配置优先于站点 YAML 和内置默认值。模型配置中 `DHUB_LLM_*` 优先于相应的 `LLM_*`。配置的项目路径以仓库根为基准，仓库 JSON 内登记的相对路径以该 JSON 所在目录为基准。

新增设置时更新读取逻辑、YAML 默认值和相关说明。凭证通过部署环境注入。 [环境变量覆盖示例](../../.env.example) · [内网项目配置](../../docs/intranet-poc-handoff.md)
