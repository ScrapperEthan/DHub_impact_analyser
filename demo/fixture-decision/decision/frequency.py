from .settings import FrequencySettings


def should_allow_campaign(
    campaign_type: str,
    contact_count: int,
    settings: FrequencySettings,
) -> bool:
    """Check the contact count that includes the proposed campaign."""
    if campaign_type == "MANDATORY" and settings.allow_mandate_campaign_override:
        return True

    return contact_count <= settings.maximum_contacts
