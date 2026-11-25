"""Main agent for requirement planning, follow-up and evidence-based reporting."""

from pydantic import ValidationError

from ..agent_runtime import AgentRuntime, State, as_json, load_system_prompt
from ..llm import LLMError
from ..report import Report, ground_report

DEMO_REQUIREMENT = (
    "Mandatory outbound campaigns should no longer bypass the frequency cap, "
    "even when Allow mandate campaign override is enabled."
)
REPORT_SCHEMA = as_json(Report.model_json_schema())


class MainProcessImpactAnalyser:
    agent_id = "main"

    def __init__(self, runtime: AgentRuntime):
        self.runtime = runtime

    def plan(self, state: State) -> dict:
        requirement = state["requirement"]
        plan = {"goal": requirement, "scope": [], "assumptions": [], "business_questions": [requirement]}
        if self.runtime.offline:
            normalized = " ".join(requirement.casefold().split()).rstrip(".")
            plan["demo_case"] = normalized == DEMO_REQUIREMENT.casefold().rstrip(".")
            return {"plan": plan}
        try:
            plan = self.runtime.ask(load_system_prompt("main-plan.md"), {"requirement": requirement})
        except LLMError as exc:
            self.runtime.note(f"需求规划受限 · {exc}")
        return {"plan": plan}

    def synthesize(self, state: State) -> dict:
        followed_up = state.get("followed_up", False)
        if self.runtime.offline:
            if state["plan"]["demo_case"] and not followed_up:
                return {"follow_up": {"agent": "code", "question": "补查调用结果、配置、测试和交付消费点"}}
            report = self.offline_report(state)
        else:
            allow_follow_up = not followed_up and self.runtime.has_tool_budget()
            try:
                response = self.runtime.ask(
                    load_system_prompt("main-report.md") + "\n\nReport JSON Schema:\n" + REPORT_SCHEMA,
                    {
                        "requirement": state["requirement"], "plan": state["plan"],
                        "domain": state.get("domain", []), "code": state.get("code", []),
                        "evidence": list(self.runtime.evidence.values()), "limitations": self.runtime.limits,
                        "allow_follow_up": allow_follow_up,
                    },
                )
                follow_up = response.get("follow_up")
                if (
                    allow_follow_up and self.runtime.has_tool_budget()
                    and isinstance(follow_up, dict) and follow_up.get("agent") in ("code", "domain")
                    and isinstance(follow_up.get("question"), str) and follow_up["question"].strip()
                ):
                    return {"follow_up": follow_up}
                report = Report.model_validate(response.get("report"))
            except (LLMError, ValidationError):
                self.runtime.note("模型未能完成结构化综合，保留已读取的证据和覆盖范围。")
                report = self.fallback(state)
        report.open_questions = list(dict.fromkeys([*report.open_questions, *self.runtime.limits]))
        return {"report": ground_report(report, self.runtime.evidence), "follow_up": None}

    def fallback(self, state: State) -> Report:
        return Report(
            requirement_summary=state["requirement"],
            summary=("本次取得了部分调查资料，请结合来源和 Trace 核对。" if self.runtime.evidence else "本次未取得可定位的原文或代码证据。"),
            coverage=[f"已读取 {len(self.runtime.evidence)} 项可定位证据。"],
            open_questions=list(self.runtime.limits),
        )

    def offline_report(self, state: State) -> Report:
        if not state["plan"]["demo_case"]:
            self.runtime.note("离线报告只覆盖内置样例。配置模型网关后可分析其他需求。")
            return self.fallback(state)
        by_page = {row.get("page_id"): key for key, row in self.runtime.evidence.items()}
        by_path = {row.get("path"): key for key, row in self.runtime.evidence.items()}
        fd = by_page.get("DEMO-FD2")
        settings = by_page.get("DEMO-SETTINGS")
        freq = by_path.get("decision/frequency.py")
        inbound = by_path.get("decision/inbound.py")
        outbound = by_path.get("decision/outbound.py")
        config = by_path.get("config/global_settings.json")
        tests = by_path.get("tests/test_frequency.py")
        delivery = by_path.get("channel/delivery.py")

        def refs(*keys):
            return [key for key in keys if key]

        def item(item_id, kind, target, effect, reason, keys, *, verified=False, related=()):
            return {
                "id": item_id, "kind": kind, "target": target, "effect": effect, "reason": reason,
                "evidence_ids": keys, "related_item_ids": list(related),
                "status": "confirmed" if verified else "potential" if keys else "unknown",
            }

        return Report.model_validate({
            "requirement_summary": "Outbound 的 Mandatory Campaign 即使启用豁免，也应遵守 Frequency Cap。",
            "summary": "拟议变更触及 P5 Step 18。实施时需隔离 Inbound 对共享逻辑的使用。",
            "assumptions": ["合成资料演示。范围为 Outbound 的 Frequency Cap 判定。"],
            "current_support": {
                "status": "change_required" if fd and freq else "unknown",
                "reason": "合成代码在豁免开启时已有绕过分支。" if fd and freq else "需要补充规则和实现证据。",
                "evidence_ids": refs(fd, freq),
            },
            "impact_items": [
                item("P5", "process", "P5 Step 18 · Frequency Cap", "direct_change",
                     "Outbound Mandatory 超额流量将从放行改为拦截。", refs(fd, freq), verified=bool(fd and freq)),
                item("Rule", "business_rule", "Mandatory Campaign 豁免", "direct_change",
                     "按 Outbound 范围收紧已有豁免规则。", refs(fd), verified=bool(fd), related=("P5",)),
                item("Code", "code", "共享 Frequency Cap 与两个入口", "verify",
                     "In/Outbound 均调用共享函数。建议核对修改点的渠道隔离。", refs(freq, inbound, outbound),
                     related=("Rule",)),
                item("Config", "configuration", "Allow mandate campaign override", "verify",
                     "核对标志位在 Outbound 与 Inbound 的适用语义，以及 Quiet Time 的独立配置。",
                     refs(settings, config, freq), related=("Rule",)),
                item("Test", "test", "Frequency Cap 回归测试", "verify",
                     "核对已有放行断言，为 Outbound 新规则和 Inbound 隔离补覆盖。", refs(tests), related=("Code",)),
                item("Delivery", "downstream", "Outbound 候选集和 Delivery", "indirect_effect",
                     "拦截可能减少传递给 Delivery 的候选活动。", refs(outbound, delivery), related=("P5",)),
            ],
            "regression_scenarios": [
                {"name": "Outbound Mandatory，豁免开启且超额", "given": "override=on, cap exceeded",
                 "before": "通过", "proposed": "拦截", "evidence_ids": refs(fd, freq, tests)},
                {"name": "Outbound Mandatory，豁免关闭且超额", "given": "override=off, cap exceeded",
                 "before": "拦截", "proposed": "拦截", "evidence_ids": refs(freq)},
                {"name": "Outbound Mandatory，未超额", "given": "cap not exceeded",
                 "before": "通过", "proposed": "通过", "evidence_ids": refs(freq)},
                {"name": "Outbound Marketing，超额", "given": "cap exceeded",
                 "before": "拦截", "proposed": "拦截", "evidence_ids": refs(freq)},
                {"name": "Inbound Mandatory，豁免开启且超额", "given": "override=on, cap exceeded",
                 "before": "通过", "proposed": "通过", "evidence_ids": refs(inbound, freq, tests)},
            ],
            "coverage": ["合成 FD2 页面、配置页和两个合成仓库。"],
            "open_questions": ["内网核对后续 FD、真实运行版本以及不同渠道的例外。"],
        })
