from __future__ import annotations

import json
import os
from pathlib import Path

from .config import load_review_config
from .github_client import GitHubClient
from .logging_utils import get_logger
from .orchestrator import run_review
from .publisher import publish
from .providers import resolve_provider_settings

LOGGER = get_logger("cli")


def _read_event(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _input(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(f"INPUT_{name.upper()}")
    if value is None or value == "":
        return default
    return value


def main() -> int:
    try:
        token = os.environ.get("GITHUB_TOKEN", "")
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        event_path = os.environ.get("GITHUB_EVENT_PATH", "")
        if not token or not repository or not event_path:
            raise RuntimeError("GITHUB_TOKEN, GITHUB_REPOSITORY, and GITHUB_EVENT_PATH must be set")

        LOGGER.info(
            "Starting review run for repository=%s event_path=%s cwd=%s",
            repository,
            event_path,
            Path.cwd(),
        )
        event = _read_event(event_path)
        LOGGER.info("Loaded event payload keys=%s", sorted(event.keys()))
        pr = event.get("pull_request") or {}
        pr_number = int(pr.get("number") or event.get("number") or 0)
        if pr_number <= 0:
            raise RuntimeError("Could not determine pull request number from the GitHub event payload")
        LOGGER.info("Resolved pull request number=%s action=%s", pr_number, event.get("action"))

        repo_root = Path.cwd()
        config_path = _input("config_path", ".ai-code-review.yml")
        config = load_review_config(
            repo_root,
            config_path,
            {
                "max_files": _input("max_files", "20"),
                "max_patch_chars": _input("max_patch_chars", "24000"),
                "post_summary": _input("post_summary", "true"),
                "min_severity_to_publish": _input("min_severity_to_publish", "low"),
            },
        )
        LOGGER.info(
            "Loaded config path=%s max_files=%s max_patch_chars=%s post_summary=%s min_severity=%s review_language=%s",
            config_path,
            config.max_files,
            config.max_patch_chars,
            config.post_summary,
            config.min_severity_to_publish,
            config.review_language,
        )
        client = GitHubClient(token=token, repository=repository)
        pr_info = client.get_pull_request(pr_number)
        LOGGER.info(
            "Fetched pull request title=%r head_sha=%s base_sha=%s",
            pr_info.title,
            pr_info.head_sha,
            pr_info.base_sha,
        )
        provider_settings = resolve_provider_settings(
            _input("provider", "deepseek") or "deepseek",
            api_key=_input("api_key"),
            model=_input("model"),
            base_url=_input("base_url"),
        )
        LOGGER.info(
            "Resolved provider provider=%s model=%s base_url=%s preferred_api=%s api_key_present=%s",
            provider_settings.provider,
            provider_settings.model,
            provider_settings.base_url,
            provider_settings.preferred_api,
            bool(provider_settings.api_key),
        )
        review = run_review(
            client=client,
            pr_number=pr_number,
            pr=pr_info,
            config=config,
            provider_settings=provider_settings,
        )
        LOGGER.info(
            "Review completed files_reviewed=%s files_skipped=%s chunks_reviewed=%s findings=%s",
            review.files_reviewed,
            review.files_skipped,
            review.chunks_reviewed,
            len(review.findings),
        )
        publish_result = publish(
            client=client,
            pr_number=pr_number,
            commit_id=pr_info.head_sha,
            findings=review.findings,
            pr_title=pr_info.title,
            post_summary=config.post_summary,
            min_severity_to_publish=config.min_severity_to_publish,
            review_language=config.review_language,
        )
        LOGGER.info(
            "Publish completed posted_inline=%s posted_summary=%s skipped_duplicate=%s",
            publish_result.posted_inline,
            publish_result.posted_summary,
            publish_result.skipped_duplicate,
        )

        output_path = os.environ.get("GITHUB_OUTPUT")
        if output_path:
            outputs = {
                "files_reviewed": review.files_reviewed,
                "chunks_reviewed": review.chunks_reviewed,
                "findings": len(review.findings),
            }
            with Path(output_path).open("a", encoding="utf-8") as handle:
                for key, value in outputs.items():
                    handle.write(f"{key}={value}\n")
            LOGGER.info("Wrote action outputs to %s", output_path)
        return 0
    except Exception:
        LOGGER.exception("Review run failed")
        raise
