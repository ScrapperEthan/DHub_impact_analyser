"""Schema for the Main Agent's optional investigation plan."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


PlanText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=400)]
TaskId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")]
OPEN_TASK_STATUSES = {"pending", "in_progress"}


class PlanTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: TaskId = Field(description="Stable ID, such as T1. Retain the ID when revising the same task.")
    agent: Literal["domain", "code"]
    question: PlanText
    status: Literal["pending", "in_progress", "completed", "blocked", "skipped"]
    note: PlanText | None = Field(default=None, description="User-facing status note, required for blocked or skipped tasks.")

    @model_validator(mode="after")
    def validate_note(self):
        if self.status in {"blocked", "skipped"} and self.note is None:
            raise ValueError("Blocked and skipped tasks require a note.")
        return self


class InvestigationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: PlanText
    tasks: list[PlanTask] = Field(min_length=1, max_length=20)
    reason: PlanText = Field(description="Brief public explanation for creating or revising this plan.")
    scope: dict[PlanText, PlanText] | None = Field(default=None, max_length=8)
    candidate_steps: list[PlanText] | None = Field(default=None, max_length=12)

    @model_validator(mode="after")
    def validate_task_ids(self):
        if len({task.id for task in self.tasks}) != len(self.tasks):
            raise ValueError("Task IDs must be unique in the plan.")
        return self
