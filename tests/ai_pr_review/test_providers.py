from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from ._path import ACTION_SRC  # noqa: F401

from ai_pr_review.providers import resolve_provider_settings


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

    def test_resolve_provider_settings_rejects_unknown_provider(self) -> None:
        with self.assertRaises(RuntimeError):
            resolve_provider_settings("unknown")


if __name__ == "__main__":
    unittest.main()
