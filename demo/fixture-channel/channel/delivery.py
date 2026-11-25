def delivery_candidates(decision_response: dict) -> list[str]:
    """Consume the filtered candidate_ids returned by select_outbound."""
    return list(decision_response["candidate_ids"])


def prepare_delivery_requests(decision_response: dict) -> list[dict]:
    return [{"campaign_id": item} for item in delivery_candidates(decision_response)]
