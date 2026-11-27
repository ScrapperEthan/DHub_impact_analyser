"""Limits and result windows shared by search and catalogue tools."""


def bounded_size(value: int, maximum: int) -> int:
    return min(max(int(value), 1), maximum)


def catalogue_page(rows: list[dict], offset: int, limit: int) -> dict:
    offset = max(0, offset)
    results = rows[offset:offset + limit]
    end = offset + len(results)
    return {
        "total": len(rows), "returned": len(results), "offset": offset,
        "has_more": end < len(rows), "next_offset": end if end < len(rows) else None,
        "results": results,
    }


def search_page(hits: list[dict], limit: int, *, status: str, scope: dict,
                warnings: list[str], unavailable: list[dict]) -> dict:
    results = hits[:limit]
    truncated = len(hits) > limit
    return {
        "status": status, "effective_scope": scope, "returned": len(results),
        "has_more": True if truncated else (False if status == "complete" else None),
        "truncated": truncated, "warnings": warnings, "unavailable": unavailable,
        "results": results,
    }
