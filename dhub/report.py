from typing import Literal

from pydantic import BaseModel, Field


class CurrentSupport(BaseModel):
    status: Literal["supported", "partially_supported", "change_required", "unknown"] = "unknown"
    reason: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class Impact(BaseModel):
    id: str
    kind: Literal["process", "business_rule", "code", "configuration", "test", "downstream", "conflict"]
    target: str
    effect: Literal["direct_change", "indirect_effect", "verify"]
    status: Literal["confirmed", "potential", "unknown"]
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)
    related_item_ids: list[str] = Field(default_factory=list)


class Scenario(BaseModel):
    name: str
    given: str = ""
    before: str | None = None
    proposed: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class Report(BaseModel):
    requirement_summary: str
    summary: str
    assumptions: list[str] = Field(default_factory=list)
    current_support: CurrentSupport = Field(default_factory=CurrentSupport)
    impact_items: list[Impact] = Field(default_factory=list)
    regression_scenarios: list[Scenario] = Field(default_factory=list)
    coverage: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


def ground_report(report: Report, evidence: dict[str, dict]) -> dict:
    for row in (report.current_support, *report.impact_items, *report.regression_scenarios):
        row.evidence_ids = list(dict.fromkeys(key for key in row.evidence_ids if key in evidence))
    if report.current_support.status != "unknown" and not report.current_support.evidence_ids:
        report.current_support = CurrentSupport(reason="This run found no traceable document or code evidence.")
    item_ids = {item.id for item in report.impact_items}
    for item in report.impact_items:
        item.related_item_ids = [key for key in item.related_item_ids if key in item_ids and key != item.id]
        if item.status == "confirmed" and not item.evidence_ids:
            item.status = "unknown"
            item.reason = "This impact needs traceable document or code evidence."
    for scenario in report.regression_scenarios:
        if not scenario.evidence_ids:
            scenario.before = None
    return {**report.model_dump(), "evidence": list(evidence.values())}
