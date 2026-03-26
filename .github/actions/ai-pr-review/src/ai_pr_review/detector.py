from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from .config import DEFAULT_LANGUAGE_EXTENSIONS, ReviewConfig


KNOWN_FILENAMES: dict[str, str] = {
    "dockerfile": "dockerfile",
    "makefile": "shell",
    "jenkinsfile": "groovy",
    "gemfile": "ruby",
    "rakefile": "ruby",
    "go.mod": "go",
    "cargo.toml": "toml",
    "package.json": "json",
    "pyproject.toml": "toml",
}

SHEBANG_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"python"), "python"),
    (re.compile(r"\b(?:ba|z)?sh\b"), "shell"),
    (re.compile(r"\bnode\b"), "javascript"),
)


@dataclass(slots=True)
class LanguageDetection:
    language: str | None
    reason: str
    confidence: float
    is_text: bool


def _looks_text(prefix: str) -> bool:
    if "\x00" in prefix:
        return False
    return True


def _normalize_prefix(prefix: str) -> str:
    lines: list[str] = []
    for raw in prefix.splitlines():
        if raw.startswith(("diff --git", "index ", "--- ", "+++ ", "@@")):
            continue
        if raw[:1] in {"+", "-", " "}:
            lines.append(raw[1:])
        else:
            lines.append(raw)
    return "\n".join(lines)


def _normalize_override_key(key: str) -> str:
    normalized = key.strip()
    if not normalized:
        return normalized
    if normalized.startswith("."):
        return normalized.lower()
    return PurePosixPath(normalized).name.lower()


def _language_from_override(path: str, config: ReviewConfig) -> str | None:
    overrides = {_normalize_override_key(key): value for key, value in config.extension_overrides.items()}
    pure_path = PurePosixPath(path)
    suffix = pure_path.suffix.lower()
    if suffix and suffix in overrides:
        return overrides[suffix]
    filename = pure_path.name.lower()
    if filename in overrides:
        return overrides[filename]
    return None


def detect_language(path: str, content_prefix: str, config: ReviewConfig) -> LanguageDetection:
    prefix = _normalize_prefix(content_prefix or "")
    if prefix and not _looks_text(prefix):
        return LanguageDetection(language=None, reason="binary-content", confidence=1.0, is_text=False)

    overridden = _language_from_override(path, config)
    if overridden:
        return LanguageDetection(language=overridden, reason="override", confidence=1.0, is_text=True)

    pure_path = PurePosixPath(path)
    suffix = pure_path.suffix.lower()
    if suffix in DEFAULT_LANGUAGE_EXTENSIONS:
        return LanguageDetection(
            language=DEFAULT_LANGUAGE_EXTENSIONS[suffix],
            reason="extension",
            confidence=0.98,
            is_text=True,
        )

    filename = pure_path.name.lower()
    if filename in KNOWN_FILENAMES:
        return LanguageDetection(language=KNOWN_FILENAMES[filename], reason="filename", confidence=0.95, is_text=True)

    first_line = prefix.splitlines()[0] if prefix else ""
    if first_line.startswith("#!"):
        for pattern, language in SHEBANG_PATTERNS:
            if pattern.search(first_line):
                return LanguageDetection(language=language, reason="shebang", confidence=0.9, is_text=True)

    stripped = prefix.lstrip()
    if not stripped:
        return LanguageDetection(language=None, reason="empty-text", confidence=0.0, is_text=True)
    if stripped.startswith(("{", "[")):
        return LanguageDetection(language="json", reason="content-sniff", confidence=0.65, is_text=True)
    if re.search(r"^(\s*[-\w]+:\s+.+)$", prefix, re.MULTILINE):
        return LanguageDetection(language="yaml", reason="content-sniff", confidence=0.55, is_text=True)
    if re.search(r"^\s*\[[^\]]+\]\s*$", prefix, re.MULTILINE):
        return LanguageDetection(language="toml", reason="content-sniff", confidence=0.55, is_text=True)
    if "<html" in stripped.lower() or stripped.lower().startswith("<!doctype html"):
        return LanguageDetection(language="html", reason="content-sniff", confidence=0.6, is_text=True)
    if re.search(r"^#{1,6}\s+\S+", prefix, re.MULTILINE):
        return LanguageDetection(language="markdown", reason="content-sniff", confidence=0.55, is_text=True)

    return LanguageDetection(language=None, reason="unknown-text", confidence=0.0, is_text=True)
