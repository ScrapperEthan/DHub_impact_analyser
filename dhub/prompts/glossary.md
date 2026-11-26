# DHub business glossary (navigation context)

Source: user-provided screenshot of the internal Decision Hub Confluence page "Glossary of Terms" (Project Management). Page ID, version and current deployment applicability are not established. The definitions below are normalized paraphrases of the visible glossary. Use these mappings to interpret requirements and formulate searches, not as current-run evidence or proof that PEGA CDH behavior is identical to DHub behavior.

## Decision Hub terminology and PEGA CDH equivalents

| Decision Hub term | PEGA CDH term | Definition |
| --- | --- | --- |
| Taxonomy | Taxonomy | Organizing structure for marketing programs, used to label and group them. |
| Journey | Issue | Top level of the taxonomy; the overall storyline. |
| Next Best Experience (NBE) | Group | Second level of the taxonomy; a specific, personalized step within a Journey. |
| Engagement Policy | Engagement Policy | Eligibility criteria and contact policy of a Journey/NBE, representing compliance requirements for a particular type of campaign/message. |
| Communication Channel | Channel | Channel through which the campaign/message is sent and from which feedback is collected. |
| Direction | Direction | Inbound or outbound direction in which the decision is made. |
| Display Location | Placement | Specific location or slot within a channel where a campaign or offer is displayed. |
| Channel Frequency Limit | Contact Policy | How often a customer can be contacted through specific channels (e.g. email, SMS, push notifications) within a given time period. |
| Contact Policy | Contact Policy | Defines how many times or how often a customer can be contacted within a specific time period. |
| Suppression Rule | Suppression Rule | Cool-off logic or suppression period applied based on customer responses or interactions with campaigns. |
| Campaign | Action | Structured marketing initiative designed to engage customers with targeted messages or offers. |
| Communication Strategy | Treatment | Specific implementation of an action, including content, format and channel through which a message or offer is delivered to the customer. |
| Conflict Group | N/A | The Conflict Grouping and Resolution feature allows campaigns across different product types to be grouped and conflicts resolved during arbitration to deliver the most relevant and valuable campaign to the customer. |

## Investigation guidance (derived from the glossary, not additional source facts)

- Use DHub terms as the primary vocabulary. PEGA CDH equivalents are useful alternative search terms when applicable; verify actual code and document terminology.
- Taxonomy identifies Journey as level one and NBE as level two. Do not infer further hierarchy or database relationships from the glossary alone.
- Distinguish Engagement Policy, Contact Policy, Channel Frequency Limit and Suppression Rule. The two DHub frequency/contact concepts both map to PEGA CDH Contact Policy; this does not establish identical DHub scopes or implementation.
- Keep Communication Channel, Direction and Display Location distinct. Do not infer which combinations exist or share implementation.
- Distinguish Campaign from Communication Strategy. Verify actual entity relationships, eligibility, cardinality and code paths rather than assuming them.
- Treat Conflict Group as an arbitration investigation lead; verify grouping, precedence and resolution behavior against applicable source material.
- Do not equate Channel Frequency Limit with Frequency Cap or assign any glossary term a precise decision step without corroborating evidence.
