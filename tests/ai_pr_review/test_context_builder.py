from __future__ import annotations

import unittest

from ._path import ACTION_SRC  # noqa: F401

from ai_pr_review.config import ReviewConfig
from ai_pr_review.context_builder import build_repo_context
from ai_pr_review.models import ChangedFile, RepoContextFile


class FakeClient:
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    def get_repo_file(self, path: str, ref: str):
        if path not in self.files:
            return None
        return RepoContextFile(path=path, content=self.files[path])


class ContextBuilderTests(unittest.TestCase):
    def test_build_repo_context_adds_c_family_includes_and_interfaces(self) -> None:
        client = FakeClient(
            {
                "src/widget.cpp": '#include "widget.h"\n#include "widget/detail.h"\n',
                "include/widget.h": "void run_widget(void);\n",
                "include/widget/detail.h": "#define WIDGET_LIMIT 7\n",
            }
        )
        config = ReviewConfig(
            context_include_repo_files=False,
            context_include_tests=False,
            context_max_files=6,
        )

        items = build_repo_context(
            client=client,
            config=config,
            ref="abc123",
            files=[ChangedFile(path="src/widget.cpp", status="modified", language="cpp")],
        )

        paths = {item.path for item in items}
        self.assertIn("src/widget.cpp", paths)
        self.assertIn("include/widget.h", paths)
        self.assertIn("include/widget/detail.h", paths)

    def test_build_repo_context_adds_header_implementation_counterpart(self) -> None:
        client = FakeClient(
            {
                "include/widget.h": "void run_widget(void);\n",
                "src/widget.c": '#include "widget.h"\nvoid run_widget(void) {}\n',
            }
        )
        config = ReviewConfig(
            context_include_repo_files=False,
            context_include_imports=False,
            context_include_tests=False,
            context_max_files=4,
        )

        items = build_repo_context(
            client=client,
            config=config,
            ref="abc123",
            files=[ChangedFile(path="include/widget.h", status="modified", language="c")],
        )

        related = {item.path: item.reason for item in items}
        self.assertEqual("Related interface", related["src/widget.c"])


if __name__ == "__main__":
    unittest.main()
