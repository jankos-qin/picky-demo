from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ._path import ACTION_SRC  # noqa: F401

from ai_pr_review.models import PullRequestInfo, ReviewChunk, ReviewPrompt
from ai_pr_review.providers import OpenAIProvider, resolve_provider_settings


class ProviderTests(unittest.TestCase):
    def test_resolve_provider_settings_prefers_explicit_inputs(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DEEPSEEK_API_KEY": "env-secret",
                "DEEPSEEK_BASE_URL": "https://env.example",
                "DEEPSEEK_CODER_MODEL": "env-model",
            },
            clear=False,
        ):
            settings = resolve_provider_settings(
                "deepseek",
                api_key="input-secret",
                base_url="https://input.example",
                model="input-model",
            )
        self.assertEqual("input-secret", settings.api_key)
        self.assertEqual("https://input.example", settings.base_url)
        self.assertEqual("input-model", settings.model)

    def test_resolve_provider_settings_reads_provider_native_envs(self) -> None:
        with patch.dict(
            os.environ,
            {
                "BCP_API_KEY": "bcp-secret",
                "BCP_BASE_URL": "https://bcp.example",
                "BCP_CODER_MODEL": "bcp-model",
            },
            clear=False,
        ):
            settings = resolve_provider_settings("bcp")
        self.assertEqual("bcp-secret", settings.api_key)
        self.assertEqual("https://bcp.example", settings.base_url)
        self.assertEqual("bcp-model", settings.model)
        self.assertEqual("chat_completions", settings.preferred_api)

    def test_resolve_provider_settings_rejects_unknown_provider(self) -> None:
        with self.assertRaises(RuntimeError):
            resolve_provider_settings("unknown")

    def test_openai_provider_falls_back_to_chat_completions_on_not_found(self) -> None:
        class NotFoundError(Exception):
            pass

        class FakeResponses:
            def create(self, **kwargs):
                raise NotFoundError("missing responses endpoint")

        class FakeChatCompletions:
            def create(self, **kwargs):
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content='{"findings":[]}')
                        )
                    ]
                )

        class FakeChat:
            def __init__(self):
                self.completions = FakeChatCompletions()

        class FakeClient:
            def __init__(self, **kwargs):
                self.responses = FakeResponses()
                self.chat = FakeChat()

        prompt = ReviewPrompt(
            pr=PullRequestInfo(number=1, title="PR title"),
            chunk=ReviewChunk(file_path="src/app.py", patch="+print('hi')\n", language="python"),
            repo_context=[],
            policy_summary="policy",
            model="deepseek-coder",
        )

        with patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=FakeClient)}):
            provider = OpenAIProvider(api_key="secret", model="deepseek-coder", base_url="https://example.com")

        self.assertEqual('{"findings":[]}', provider.review(prompt))

    def test_openai_provider_uses_chat_completions_when_preferred(self) -> None:
        calls: list[dict[str, object]] = []

        class FakeResponses:
            def create(self, **kwargs):
                raise AssertionError("responses endpoint should not be used")

        class FakeChatCompletions:
            def create(self, **kwargs):
                calls.append(kwargs)
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content='{"findings":[]}')
                        )
                    ]
                )

        class FakeChat:
            def __init__(self):
                self.completions = FakeChatCompletions()

        class FakeClient:
            def __init__(self, **kwargs):
                self.responses = FakeResponses()
                self.chat = FakeChat()

        prompt = ReviewPrompt(
            pr=PullRequestInfo(number=1, title="PR title"),
            chunk=ReviewChunk(file_path="src/app.py", patch="+print('hi')\n", language="python"),
            repo_context=[],
            policy_summary="policy",
            model="deepseek-coder",
        )

        with patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=FakeClient)}):
            provider = OpenAIProvider(
                api_key="secret",
                model="deepseek-coder",
                base_url="https://example.com",
                preferred_api="chat_completions",
            )

        self.assertEqual('{"findings":[]}', provider.review(prompt))
        self.assertEqual({"type": "json_object"}, calls[0]["response_format"])


if __name__ == "__main__":
    unittest.main()
