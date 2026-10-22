import re


def is_abstract(text: str) -> bool:
    return bool(re.fullmatch(r"a\s*b\s*s\s*t\s*r\s*a\s*c\s*t\s*[:：]?|摘要", text.strip(), re.I))


def abstract_prefix(text: str) -> re.Match[str] | None:
    return re.match(r"^(?:a\s*b\s*s\s*t\s*r\s*a\s*c\s*t\b|摘要)\s*[:：.—-]?\s*", text, re.I)


def is_auxiliary(text: str) -> bool:
    return bool(
        re.match(
            r"^(?:Code|Data|Extended Version|Project|Supplementary)\s*[-:：]|^https?://", text, re.I
        )
    )
