"""Read a bounded source window and produce code evidence."""

from pydantic import StrictInt

from ..repositories import code_lines
from .utils.source import SourceError, relative_path


TRACE_FIELDS = ("repo_id", "path", "start_line", "end_line", "column_offset", "max_chars")
MAX_CODE_READ_CHARS = 10_000


def read_file(
    self, repo_id: str, path: str, start_line: StrictInt = 1, end_line: StrictInt | None = None,
    column_offset: StrictInt = 0, max_chars: StrictInt = MAX_CODE_READ_CHARS,
) -> dict:
    """Read numbered source lines from the pinned commit. Cite the returned evidence ID.

    column_offset is a zero-based character offset on start_line. If truncated, pass
    next_start_line and next_column_offset to continue within the requested range.
    Use max_chars to collect a small evidence window around the relevant code.
    """
    repo = self._repo(repo_id)
    path = relative_path(path)
    content = code_lines(repo.read(path))
    start = start_line
    if (
        type(start) is not int or not 1 <= start <= max(1, len(content))
        or (end_line is not None and (type(end_line) is not int or end_line < start))
    ):
        raise SourceError("invalid_line_range", "Line range is outside the source.")
    if (
        type(column_offset) is not int or column_offset < 0
        or (column_offset and (not content or column_offset >= len(content[start - 1])))
    ):
        raise SourceError("invalid_column_offset", "Use a character offset inside start_line.")
    if type(max_chars) is not int or not 1 <= max_chars <= MAX_CODE_READ_CHARS:
        raise SourceError("invalid_read_size", f"Use max_chars between 1 and {MAX_CODE_READ_CHARS}.")
    requested_end = min(end_line, len(content)) if end_line is not None else len(content)
    end = min(requested_end, start + 119)
    next_line = end + 1 if end < len(content) else None
    next_column = 0
    remaining = max_chars
    lines = []
    end_offset = 0
    for index in range(start, end + 1):
        if not remaining:
            next_line = index
            break
        line = content[index - 1]
        offset = column_offset if index == start else 0
        text = line[offset:offset + remaining]
        end_offset = offset + len(text)
        lines.append({"line": index, "text": text})
        remaining -= len(text)
        if end_offset < len(line):
            next_line = index
            next_column = end_offset
            break
    return {
        "repo_id": repo_id, "branch": repo.branch, "commit_sha": repo.commit_sha, "path": path,
        "start_line": start, "end_line": lines[-1]["line"] if lines else end,
        "column_offset": column_offset, "end_column_offset": end_offset,
        "total_lines": len(content), "line_truncated": bool(column_offset or next_column),
        "lines": lines, "truncated": next_line is not None and next_line <= requested_end,
        "next_start_line": next_line, "next_column_offset": next_column if next_line is not None else None,
    }


def code_evidence(result: dict) -> dict | None:
    if not result.get("lines"):
        return None
    fields = {
        key: result.get(key)
        for key in ("repo_id", "branch", "commit_sha", "path", "start_line", "end_line")
    }
    excerpt = "\n".join(f"{line['line']}  {line['text']}" for line in result["lines"])
    return {
        **fields, "source_type": "code", "excerpt": excerpt,
        "column_offset": result["column_offset"], "end_column_offset": result["end_column_offset"],
        "line_truncated": result["line_truncated"],
    }
