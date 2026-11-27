"""Read and validate paths within a configured source snapshot."""

from pathlib import Path, PurePosixPath


class SourceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


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
