from .frequency import should_allow_campaign
from .settings import FrequencySettings


def select_inbound(campaigns: list[dict], settings: FrequencySettings) -> list[str]:
    return [
        campaign["id"] for campaign in campaigns
        if should_allow_campaign(campaign["type"], campaign["contact_count"], settings)
    ]
