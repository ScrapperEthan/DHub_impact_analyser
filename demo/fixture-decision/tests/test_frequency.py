import unittest

from decision.frequency import should_allow_campaign
from decision.inbound import select_inbound
from decision.outbound import select_outbound
from decision.settings import FrequencySettings


class FrequencyCapTests(unittest.TestCase):
    def test_shared_default_bypass_for_mandatory_when_over_cap(self):
        settings = FrequencySettings(3, allow_mandate_campaign_override=True)
        self.assertTrue(should_allow_campaign("MANDATORY", 4, settings))

    def test_outbound_and_inbound_currently_share_bypass(self):
        settings = FrequencySettings(3, allow_mandate_campaign_override=True)
        campaigns = [{"id": "demo", "type": "MANDATORY", "contact_count": 4}]
        self.assertEqual(select_outbound(campaigns, settings), {"candidate_ids": ["demo"]})
        self.assertEqual(select_inbound(campaigns, settings), ["demo"])

    def test_block_when_override_is_disabled(self):
        self.assertFalse(should_allow_campaign("MANDATORY", 4, FrequencySettings(3, False)))

    def test_block_marketing_when_over_cap(self):
        self.assertFalse(should_allow_campaign("MARKETING", 4, FrequencySettings(3, True)))
