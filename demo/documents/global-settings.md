# Global Settings (Demo)

## Frequency Cap

The configuration object is `frequency_cap`. The `maximum_contacts` field sets the contact limit, and `allow_mandate_campaign_override` controls the exemption for Mandatory Campaigns. These settings apply to Inbound and Outbound flows that use this configuration.

| Key | Demo default |
| --- | --- |
| frequency_cap.maximum_contacts | 3 |
| frequency_cap.allow_mandate_campaign_override | true |

## Quiet Time

`quiet_time` has its own `enabled`, `start_hour`, and `end_hour` fields. Frequency Cap does not read these fields. The shared Global Settings file can contain both configurations.
