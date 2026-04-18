from __future__ import annotations

import unittest

from ._path import ACTION_SRC  # noqa: F401

from ai_pr_review.prompting import build_prompt
from ai_pr_review.models import ReviewChunk


class PromptingTests(unittest.TestCase):
    def test_build_prompt_includes_maintainability_and_header_checklist(self) -> None:
        prompt = build_prompt(
            pr_title="Adjust public API",
            pr_body=None,
            chunk=ReviewChunk(
                file_path="include/widget.h",
                patch="@@ -1 +1 @@\n-void run(void);\n+void run(int timeout_ms);\n",
                language="cpp",
            ),
            repo_context=[],
            policy_summary="policy",
            review_language="en",
        )

        self.assertIn("magic numbers", prompt)
        self.assertIn("public API, declaration, and header-surface changes", prompt)
        self.assertIn("declaration/definition mismatches", prompt)


if __name__ == "__main__":
    unittest.main()
