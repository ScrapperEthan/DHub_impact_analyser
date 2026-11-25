from .frequency import should_allow_campaign
from .settings import FrequencySettings


def select_outbound(campaigns: list[dict], settings: FrequencySettings) -> dict:
    candidate_ids = [
        campaign["id"] for campaign in campaigns
        if should_allow_campaign(campaign["type"], campaign["contact_count"], settings)
    ]
    return {"candidate_ids": candidate_ids}
