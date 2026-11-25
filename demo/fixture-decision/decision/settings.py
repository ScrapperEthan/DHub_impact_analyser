import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FrequencySettings:
    maximum_contacts: int
    allow_mandate_campaign_override: bool


def load_frequency_settings(config_path: Path) -> FrequencySettings:
    data = json.loads(config_path.read_text(encoding="utf-8"))["frequency_cap"]
    return FrequencySettings(
        maximum_contacts=data["maximum_contacts"],
        allow_mandate_campaign_override=data["allow_mandate_campaign_override"],
    )
