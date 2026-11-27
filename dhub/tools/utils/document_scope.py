"""Document scope matching and snapshot coverage."""

from ...sources.documents import Knowledge


def matches_scope(metadata: dict, feature_drop: str | None, ancestor: str | None) -> bool:
    return (
        (not feature_drop or metadata["feature_drop"].casefold() == feature_drop.casefold())
        and (not ancestor or any(ancestor.casefold() in item.casefold() for item in metadata["ancestors"]))
    )


def document_coverage(knowledge: Knowledge) -> tuple[str, list[str]]:
    if knowledge.error:
        return "unavailable", [knowledge.error]
    if knowledge.skipped_pages:
        return "partial", [f"{len(knowledge.skipped_pages)} document snapshot entries could not be loaded."]
    return "complete", []
