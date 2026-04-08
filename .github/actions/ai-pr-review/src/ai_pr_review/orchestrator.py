from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .config import ReviewConfig
from .context_builder import build_repo_context
from .diff import build_review_chunks, should_include_file
from .github_client import GitHubClient
from .logging_utils import get_logger
from .models import Finding, ReviewPrompt
from .prompting import build_policy_summary
from .providers import (
    OpenAIProvider,
    ProviderAdapter,
    ProviderSettings,
    UnsupportedProvider,
)
from .publisher import finding_fingerprint

LOGGER = get_logger("orchestrator")


@dataclass(slots=True)
class ReviewRunResult:
    findings: list[Finding]
    chunks_reviewed: int
    files_reviewed: int
    files_skipped: int


def _provider_for(settings: ProviderSettings) -> ProviderAdapter:
    if settings.provider in {"openai", "deepseek", "bcp"}:
        return OpenAIProvider(
            api_key=settings.api_key,
            model=settings.model,
            base_url=settings.base_url,
            preferred_api=settings.preferred_api,
        )
    return UnsupportedProvider(settings.provider)


def _extract_json(text: str) -> Any:
    candidate = text.strip()
    if "```" in candidate:
        parts = candidate.split("```")
        for part in parts:
            stripped = part.strip()
            if stripped.startswith("json"):
                candidate = stripped[4:].strip()
                break
            if stripped.startswith("{") or stripped.startswith("["):
                candidate = stripped
                break
    if candidate.startswith("```") and candidate.endswith("```"):
        candidate = candidate[3:-3].strip()
    return json.loads(candidate)


def normalize_findings(payload: Any, default_path: str) -> list[Finding]:
    if isinstance(payload, dict):
        items = payload.get("findings", [])
    else:
        items = payload
    findings: list[Finding] = []
    if not isinstance(items, list):
        return findings
    for item in items:
        if not isinstance(item, dict):
            continue
        line_value = item.get("line")
        if isinstance(line_value, str) and line_value.strip():
            try:
                line_value = int(line_value.strip())
            except ValueError:
                line_value = None
        elif not isinstance(line_value, int):
            line_value = None
        finding = Finding(
            path=str(item.get("path") or default_path),
            line=line_value,
            severity=str(item.get("severity", "low")).strip().lower(),
            confidence=float(item.get("confidence", 0.5) or 0.5),
            title=str(item.get("title", "")).strip(),
            body=str(item.get("body", "")).strip(),
            suggested_fix=str(item.get("suggested_fix", "")).strip(),
        ).normalized()
        if not finding.title or not finding.body:
            continue
        finding.fingerprint = finding_fingerprint(finding)
        findings.append(finding)
    return findings


def run_review(
    *,
    client: GitHubClient,
    pr_number: int,
    pr: Any,
    config: ReviewConfig,
    provider_settings: ProviderSettings,
) -> ReviewRunResult:
    provider = _provider_for(provider_settings)
    files = client.list_pull_files(pr_number)
    LOGGER.info("Fetched changed files count=%s for pr=%s", len(files), pr_number)
    filtered = [file for file in files if should_include_file(file, config)]
    skipped_by_filter = len(files) - len(filtered)
    filtered = filtered[: config.max_files]
    LOGGER.info(
        "Selected files for review filtered=%s skipped_by_filter=%s max_files=%s",
        len(filtered),
        skipped_by_filter,
        config.max_files,
    )
    repo_context = build_repo_context(
        client=client,
        config=config,
        ref=getattr(pr, "head_sha", None),
        files=filtered,
    )
    LOGGER.info("Built repo context items=%s", len(repo_context))
    chunks = build_review_chunks(filtered, config.max_patch_chars)
    LOGGER.info("Built review chunks count=%s max_patch_chars=%s", len(chunks), config.max_patch_chars)
    policy_summary = build_policy_summary(config)

    findings: list[Finding] = []
    for index, chunk in enumerate(chunks, start=1):
        LOGGER.info(
            "Reviewing chunk %s/%s file=%s language=%s patch_chars=%s",
            index,
            len(chunks),
            chunk.file_path,
            chunk.language,
            len(chunk.patch),
        )
        prompt = ReviewPrompt(
            pr=pr,
            chunk=chunk,
            repo_context=repo_context,
            policy_summary=policy_summary,
            model=provider_settings.model,
            review_language=config.review_language,
        )
        raw = provider.review(prompt)
        LOGGER.info("Provider returned %s chars for file=%s", len(raw), chunk.file_path)
        try:
            payload = _extract_json(raw)
        except Exception:
            LOGGER.exception(
                "Failed to parse provider response as JSON for file=%s chunk=%s/%s",
                chunk.file_path,
                index,
                len(chunks),
            )
            continue
        normalized = normalize_findings(payload, chunk.file_path)
        LOGGER.info(
            "Normalized findings count=%s for file=%s chunk=%s/%s",
            len(normalized),
            chunk.file_path,
            index,
            len(chunks),
        )
        findings.extend(normalized)

    unique: list[Finding] = []
    seen: set[str] = set()
    for finding in findings:
        if finding.fingerprint in seen:
            continue
        seen.add(finding.fingerprint)
        unique.append(finding)
    LOGGER.info("Deduped findings raw=%s unique=%s", len(findings), len(unique))

    return ReviewRunResult(
        findings=unique,
        chunks_reviewed=len(chunks),
        files_reviewed=len(filtered),
        files_skipped=max(0, len(files) - len(filtered)),
    )
