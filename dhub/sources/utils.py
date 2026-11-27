"""Path checks, bounded reads and text matching for source snapshots."""

import re
from pathlib import Path, PurePosixPath


DEMO_ROOT = Path(__file__).resolve().parents[2] / "demo"
_URL_CREDENTIALS = re.compile(r"(?<=://)[^/@\s]+@")
_BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]+")
_SECRET_VALUE = re.compile(r"(?i)\b((?:api[_-]?key|access[_-]?token|token|password|secret)\s*[=:]\s*)[^\s,;]+")


class SourceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def describe_error(error: BaseException | str, limit: int = 240) -> str:
    """Return a short, single-line reason with credentials masked."""
    if isinstance(error, BaseException):
        message = str(error)
        error = f"{type(error).__name__}: {message}" if message else type(error).__name__
    text = " ".join(error.split())
    text = _URL_CREDENTIALS.sub("***@", text)
    text = _BEARER.sub(lambda match: match[1] + "[redacted]", text)
    text = _SECRET_VALUE.sub(lambda match: match[1] + "[redacted]", text)
    return text[:limit]


def relative_path(value: str) -> str:
    value = str(value).replace("\\", "/")
    path = PurePosixPath(value)
    if (
        not path.parts or path.is_absolute() or ".." in path.parts
        or ":" in value or any(ord(char) < 32 for char in value)
    ):
        raise SourceError("invalid_path", "Use a relative path inside the configured source.")
    return path.as_posix()


def path_in_source(root: Path, relative: str) -> Path:
    candidate = (root / relative_path(relative)).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise SourceError("invalid_path", "Path is outside the configured source.")
    return candidate


def read_source_text(path: Path, max_bytes: int) -> str:
    with path.open("rb") as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise SourceError("source_too_large", "Source exceeds the configured file size limit.")
    return data.decode("utf-8-sig")


def match_excerpt(text: str, start: int, end: int, limit: int) -> str:
    if len(text) <= limit:
        return text
    width = limit - 2  # Reserve space for ellipses on either side.
    left = min(max(0, start - max(0, (width - (end - start)) // 2)), len(text) - width)
    right = left + width
    return ("…" if left else "") + text[left:right] + ("…" if right < len(text) else "")


def query_tokens(query: str) -> set[str]:
    tokens = set()
    for word in re.findall(r"[^\W_]+", query.casefold()):
        if re.fullmatch(r"[\u3400-\u9fff]+", word) and len(word) > 2:
            tokens.update(word[index:index + 2] for index in range(len(word) - 1))
        else:
            tokens.add(word)
    return tokens
