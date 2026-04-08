from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .logging_utils import get_logger
from .models import ChangedFile, PullRequestInfo, RepoContextFile

LOGGER = get_logger("github")


@dataclass(slots=True)
class GitHubComment:
    id: int
    body: str
    path: str | None = None
    line: int | None = None
    user_login: str | None = None
    url: str | None = None


class GitHubClient:
    def __init__(self, token: str, repository: str, api_base: str = "https://api.github.com") -> None:
        self._token = token
        self._repository = repository
        self._api_base = api_base.rstrip("/")
        self._file_cache: dict[tuple[str, str], RepoContextFile | None] = {}
        self._commit_files_cache: dict[str, list[ChangedFile]] = {}

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        LOGGER.info("GitHub API request method=%s path=%s payload_keys=%s", method, path, sorted((payload or {}).keys()))
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            f"{self._api_base}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request) as response:
                body = response.read().decode("utf-8")
                LOGGER.info("GitHub API response method=%s path=%s status=%s", method, path, getattr(response, "status", "unknown"))
                return json.loads(body) if body else None
        except HTTPError as exc:
            body = exc.read().decode("utf-8") if exc.fp else ""
            LOGGER.error("GitHub API request failed method=%s path=%s status=%s body=%s", method, path, exc.code, body)
            raise RuntimeError(f"GitHub API request failed: {method} {path}: {exc.code} {body}") from exc

    def get_pull_request(self, number: int) -> PullRequestInfo:
        data = self._request("GET", f"/repos/{self._repository}/pulls/{number}")
        return PullRequestInfo(
            number=number,
            title=data.get("title", ""),
            body=data.get("body"),
            head_sha=(data.get("head") or {}).get("sha"),
            base_sha=(data.get("base") or {}).get("sha"),
            html_url=data.get("html_url"),
        )

    def list_pull_files(self, number: int) -> list[ChangedFile]:
        files: list[ChangedFile] = []
        page = 1
        while True:
            data = self._request(
                "GET",
                f"/repos/{self._repository}/pulls/{number}/files?per_page=100&page={page}",
            )
            if not data:
                break
            for item in data:
                files.append(
                    ChangedFile(
                        path=item.get("filename", ""),
                        status=item.get("status", ""),
                        additions=item.get("additions", 0) or 0,
                        deletions=item.get("deletions", 0) or 0,
                        changes=item.get("changes", 0) or 0,
                        patch=item.get("patch"),
                        raw_url=item.get("raw_url"),
                        blob_url=item.get("blob_url"),
                        is_binary=not bool(item.get("patch")) and item.get("status") != "removed",
                        metadata=item,
                    )
                )
            if len(data) < 100:
                break
            page += 1
        return files

    def list_pull_commits(self, number: int) -> list[str]:
        commits: list[str] = []
        page = 1
        while True:
            data = self._request(
                "GET",
                f"/repos/{self._repository}/pulls/{number}/commits?per_page=100&page={page}",
            )
            if not data:
                break
            for item in data:
                sha = str(item.get("sha", "")).strip()
                if sha:
                    commits.append(sha)
            if len(data) < 100:
                break
            page += 1
        return commits

    def get_commit_files(self, commit_sha: str) -> list[ChangedFile]:
        if commit_sha in self._commit_files_cache:
            return list(self._commit_files_cache[commit_sha])
        data = self._request(
            "GET",
            f"/repos/{self._repository}/commits/{quote(commit_sha, safe='')}",
        )
        files: list[ChangedFile] = []
        for item in (data or {}).get("files", []) or []:
            metadata = dict(item)
            path = item.get("filename", "")
            previous = item.get("previous_filename")
            if previous:
                metadata["previous_filename"] = previous
            files.append(
                ChangedFile(
                    path=path,
                    status=item.get("status", ""),
                    additions=item.get("additions", 0) or 0,
                    deletions=item.get("deletions", 0) or 0,
                    changes=item.get("changes", 0) or 0,
                    patch=item.get("patch"),
                    raw_url=item.get("raw_url"),
                    blob_url=item.get("blob_url"),
                    is_binary=not bool(item.get("patch")) and item.get("status") != "removed",
                    metadata=metadata,
                )
            )
        self._commit_files_cache[commit_sha] = list(files)
        return files

    def get_repo_file(self, path: str, ref: str) -> RepoContextFile | None:
        cache_key = (path, ref)
        if cache_key in self._file_cache:
            return self._file_cache[cache_key]
        encoded_path = quote(path, safe="/")
        data = self._request(
            "GET",
            f"/repos/{self._repository}/contents/{encoded_path}?ref={quote(ref, safe='')}",
        )
        if not isinstance(data, dict):
            self._file_cache[cache_key] = None
            return None
        if data.get("encoding") != "base64" or "content" not in data:
            self._file_cache[cache_key] = None
            return None
        content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        result = RepoContextFile(path=path, content=content)
        self._file_cache[cache_key] = result
        return result

    def list_review_comments(self, number: int) -> list[GitHubComment]:
        data = self._request("GET", f"/repos/{self._repository}/pulls/{number}/comments?per_page=100")
        comments: list[GitHubComment] = []
        for item in data or []:
            comments.append(
                GitHubComment(
                    id=item.get("id", 0),
                    body=item.get("body", ""),
                    path=item.get("path"),
                    line=item.get("line"),
                    user_login=(item.get("user") or {}).get("login"),
                    url=item.get("url"),
                )
            )
        return comments

    def list_issue_comments(self, number: int) -> list[GitHubComment]:
        data = self._request("GET", f"/repos/{self._repository}/issues/{number}/comments?per_page=100")
        comments: list[GitHubComment] = []
        for item in data or []:
            comments.append(
                GitHubComment(
                    id=item.get("id", 0),
                    body=item.get("body", ""),
                    user_login=(item.get("user") or {}).get("login"),
                    url=item.get("url"),
                )
            )
        return comments

    def create_review_comment(
        self, number: int, body: str, path: str, line: int, commit_id: str
    ) -> None:
        self._request(
            "POST",
            f"/repos/{self._repository}/pulls/{number}/comments",
            {
                "body": body,
                "commit_id": commit_id,
                "path": path,
                "line": line,
                "side": "RIGHT",
            },
        )

    def create_pull_review(
        self,
        number: int,
        commit_id: str,
        body: str,
        comments: list[dict[str, Any]],
    ) -> None:
        payload: dict[str, Any] = {
            "commit_id": commit_id,
            "event": "COMMENT",
            "comments": comments,
        }
        if body.strip():
            payload["body"] = body
        self._request(
            "POST",
            f"/repos/{self._repository}/pulls/{number}/reviews",
            payload,
        )

    def create_issue_comment(self, number: int, body: str) -> None:
        self._request(
            "POST",
            f"/repos/{self._repository}/issues/{number}/comments",
            {"body": body},
        )
