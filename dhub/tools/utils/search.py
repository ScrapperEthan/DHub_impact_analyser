"""Keyword matching and excerpts for source discovery."""

import re


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
