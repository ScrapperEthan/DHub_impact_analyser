# Decisioning Process (Demo)

This synthetic document demonstrates how the DHub PoC cites decision process steps.

## Decision Process Baseline

| Phase | Purpose |
| --- | --- |
| P1 Initialization | Load active Campaigns and select them by channel |
| P2 Multi-Layered Eligibility Filtering | Apply Campaign, Channel, and Communication Strategy eligibility criteria |
| P3 Business Value Calculation | Calculate the Arbitration Score for each candidate Campaign |
| P4 Contact Permissions | Apply Contact Policy and Suppression Rules |
| P5 Final Arbitration & Fatigue Management | Rank candidates, resolve conflicts, and check Frequency Cap and Quiet Time |
| P6 Delivery | Pass eligible Campaign candidates to the appropriate channel |

## Phase 5

### Step 18

Frequency Cap checks how many contacts there would be after the proposed contact. If the count would exceed the limit, it blocks the Campaign.

This demo rule applies to both Inbound and Outbound. A MANDATORY Campaign can bypass the Frequency Cap when Allow mandate campaign override is enabled in Global Settings, even if the contact count would exceed the limit. The normal cap applies when the override is disabled or the Campaign type is MARKETING.

See the Frequency Cap section in DEMO-SETTINGS v1 for the configuration fields. Quiet Time has its own settings.

## Phase 6

### Step 21

Outbound Delivery receives candidate IDs that passed eligibility and Frequency Cap checks. Campaigns blocked by the cap are excluded from the delivery candidate list. The final send outcome depends on channel processing.
