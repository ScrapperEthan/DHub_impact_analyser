# Global Settings（演示）

## Frequency Cap

配置对象为 frequency_cap。maximum_contacts 设置次数上限，allow_mandate_campaign_override 控制 Mandatory Campaign 的豁免，适用于使用这份配置的 Inbound 和 Outbound。

| Key | Demo default |
| --- | --- |
| frequency_cap.maximum_contacts | 3 |
| frequency_cap.allow_mandate_campaign_override | true |

## Quiet Time

quiet_time 以独立的 enabled、start_hour 和 end_hour 字段配置。Frequency Cap 不读取这些字段。Shared Global Settings 配置文件可同时包含两组配置。
