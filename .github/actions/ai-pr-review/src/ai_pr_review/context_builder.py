from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Iterable

from .config import DEFAULT_CONTEXT_FILES, ReviewConfig
from .detector import detect_language
from .models import ChangedFile, RepoContextFile


IMPORT_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "python": (
        re.compile(r"^\s*from\s+([A-Za-z0-9_\.]+)\s+import\s+", re.MULTILINE),
        re.compile(r"^\s*import\s+([A-Za-z0-9_\.]+)", re.MULTILINE),
    ),
    "javascript": (
        re.compile(r"from\s+['\"]([^'\"]+)['\"]"),
        re.compile(r"require\(['\"]([^'\"]+)['\"]\)"),
    ),
    "jsx": (
        re.compile(r"from\s+['\"]([^'\"]+)['\"]"),
        re.compile(r"require\(['\"]([^'\"]+)['\"]\)"),
    ),
    "typescript": (
        re.compile(r"from\s+['\"]([^'\"]+)['\"]"),
        re.compile(r"require\(['\"]([^'\"]+)['\"]\)"),
    ),
    "tsx": (
        re.compile(r"from\s+['\"]([^'\"]+)['\"]"),
        re.compile(r"require\(['\"]([^'\"]+)['\"]\)"),
    ),
    "go": (re.compile(r"^\s*import\s+\"([^\"]+)\"", re.MULTILINE),),
}

SOURCE_EXTENSIONS = (".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".rb", ".php")
TEST_MARKERS = ("test_", "_test", ".spec.", ".test.")
CONFIG_FILENAMES = (
    "package.json",
    "tsconfig.json",
    "pyproject.toml",
    "requirements.txt",
    "Cargo.toml",
    "go.mod",
    "eslint.config.js",
    ".eslintrc",
)


def _within_budget(items: list[RepoContextFile], max_files: int, max_bytes: int) -> bool:
    if len(items) >= max_files:
        return False
    return sum(len(item.content) for item in items) < max_bytes


def _add_context_item(
    *,
    items: list[RepoContextFile],
    seen_paths: set[str],
    candidate: RepoContextFile | None,
    max_files: int,
    max_bytes: int,
) -> None:
    if candidate is None or candidate.path in seen_paths:
        return
    new_total = sum(len(item.content) for item in items) + len(candidate.content)
    if len(items) >= max_files or new_total > max_bytes:
        return
    seen_paths.add(candidate.path)
    items.append(candidate)


def _guess_related_configs(path: str) -> list[str]:
    pure_path = PurePosixPath(path)
    candidates: list[str] = []
    current = pure_path.parent
    while current != current.parent:
        base = current.as_posix()
        for filename in CONFIG_FILENAMES:
            candidates.append(f"{base}/{filename}" if base != "." else filename)
        if base in {"", "."}:
            break
        current = current.parent
    return candidates


def _guess_sibling_tests(path: str, language: str | None) -> list[str]:
    pure_path = PurePosixPath(path)
    suffix = pure_path.suffix
    stem = pure_path.stem
    parent = pure_path.parent.as_posix()
    candidates: list[str] = []
    if suffix in SOURCE_EXTENSIONS:
        prefix = f"{parent}/" if parent != "." else ""
        candidates.extend(
            [
                f"{prefix}test_{stem}{suffix}",
                f"{prefix}{stem}_test{suffix}",
                f"{prefix}{stem}.test{suffix}",
                f"{prefix}{stem}.spec{suffix}",
                f"{prefix}tests/{stem}{suffix}",
                f"{prefix}__tests__/{stem}{suffix}",
            ]
        )
    if language == "python":
        parts = pure_path.parts
        if "src" in parts:
            src_index = parts.index("src")
            tail = parts[src_index + 1 :]
            if tail:
                candidates.append(str(PurePosixPath("tests", *tail[:-1], f"test_{stem}{suffix}")))
    return candidates


def _candidate_import_paths(import_name: str, base_path: str, language: str | None) -> list[str]:
    candidates: list[str] = []
    if language in {"javascript", "jsx", "typescript", "tsx"}:
        if import_name.startswith("."):
            base_dir = PurePosixPath(base_path).parent
            target = (base_dir / import_name).as_posix()
        else:
            target = import_name
        for suffix in (".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.tsx", "/index.js", "/index.jsx"):
            candidates.append(f"{target}{suffix}" if not target.endswith(suffix) else target)
    elif language == "python":
        base_dir = PurePosixPath(base_path).parent
        if import_name.startswith("."):
            depth = len(import_name) - len(import_name.lstrip("."))
            remainder = import_name.lstrip(".")
            target_dir = base_dir
            for _ in range(max(0, depth - 1)):
                target_dir = target_dir.parent
            target = target_dir / remainder.replace(".", "/") if remainder else target_dir
            target_path = target.as_posix()
        else:
            target_path = import_name.replace(".", "/")
        candidates.append(target_path + ".py")
        candidates.append(target_path + "/__init__.py")
    elif language == "go":
        candidates.append(import_name)
    return candidates


def _extract_import_candidates(file_content: str, file_path: str, language: str | None) -> list[str]:
    if not language:
        return []
    patterns = IMPORT_PATTERNS.get(language, ())
    found: list[str] = []
    for pattern in patterns:
        found.extend(match.group(1) for match in pattern.finditer(file_content))
    candidates: list[str] = []
    for item in found:
        if item.startswith(("http://", "https://")):
            continue
        if language in {"javascript", "jsx", "typescript", "tsx"} and not item.startswith((".", "/")):
            continue
        candidates.extend(_candidate_import_paths(item, file_path, language))
    return candidates


def build_repo_context(
    *,
    client,
    config: ReviewConfig,
    ref: str | None,
    files: Iterable[ChangedFile],
) -> list[RepoContextFile]:
    if not ref:
        return []

    items: list[RepoContextFile] = []
    seen_paths: set[str] = set()

    def safe_get(path: str) -> RepoContextFile | None:
        try:
            return client.get_repo_file(path, ref)
        except Exception:
            return None

    if config.context_include_repo_files:
        for path in [*DEFAULT_CONTEXT_FILES, *config.context_files]:
            if not _within_budget(items, config.context_max_files, config.context_max_bytes):
                break
            ctx = safe_get(path)
            if ctx is not None:
                ctx.reason = "Repo manifest"
                ctx.language = detect_language(ctx.path, ctx.content[:4096], config).language
            _add_context_item(
                items=items,
                seen_paths=seen_paths,
                candidate=ctx,
                max_files=config.context_max_files,
                max_bytes=config.context_max_bytes,
            )

    for changed_file in files:
        if not _within_budget(items, config.context_max_files, config.context_max_bytes):
            break
        file_content = safe_get(changed_file.path)
        if file_content is not None:
            file_content.reason = "Changed file context"
            file_content.language = changed_file.language or detect_language(
                changed_file.path, file_content.content[:4096], config
            ).language
            _add_context_item(
                items=items,
                seen_paths=seen_paths,
                candidate=file_content,
                max_files=config.context_max_files,
                max_bytes=config.context_max_bytes,
            )
            source_content = file_content.content
        else:
            source_content = ""

        if config.context_include_imports and source_content:
            for path in _extract_import_candidates(source_content[:12000], changed_file.path, changed_file.language):
                if not _within_budget(items, config.context_max_files, config.context_max_bytes):
                    break
                ctx = safe_get(path)
                if ctx is None:
                    continue
                ctx.reason = "Imported module"
                ctx.language = detect_language(ctx.path, ctx.content[:4096], config).language
                _add_context_item(
                    items=items,
                    seen_paths=seen_paths,
                    candidate=ctx,
                    max_files=config.context_max_files,
                    max_bytes=config.context_max_bytes,
                )

        for path in _guess_related_configs(changed_file.path):
            if not _within_budget(items, config.context_max_files, config.context_max_bytes):
                break
            ctx = safe_get(path)
            if ctx is None:
                continue
            ctx.reason = "Related config"
            ctx.language = detect_language(ctx.path, ctx.content[:4096], config).language
            _add_context_item(
                items=items,
                seen_paths=seen_paths,
                candidate=ctx,
                max_files=config.context_max_files,
                max_bytes=config.context_max_bytes,
            )

        if config.context_include_tests:
            for path in _guess_sibling_tests(changed_file.path, changed_file.language):
                if not _within_budget(items, config.context_max_files, config.context_max_bytes):
                    break
                ctx = safe_get(path)
                if ctx is None:
                    continue
                ctx.reason = "Sibling test"
                ctx.language = detect_language(ctx.path, ctx.content[:4096], config).language
                _add_context_item(
                    items=items,
                    seen_paths=seen_paths,
                    candidate=ctx,
                    max_files=config.context_max_files,
                    max_bytes=config.context_max_bytes,
                )

    return items
