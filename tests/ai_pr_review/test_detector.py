from __future__ import annotations

import unittest

from ._path import ACTION_SRC  # noqa: F401

from ai_pr_review.config import ReviewConfig
from ai_pr_review.detector import detect_language


class DetectorTests(unittest.TestCase):
    def test_detect_language_from_extension_filename_shebang_and_content(self) -> None:
        config = ReviewConfig()
        self.assertEqual("typescript", detect_language("src/app.ts", "", config).language)
        self.assertEqual("dockerfile", detect_language("Dockerfile", "", config).language)
        self.assertEqual("python", detect_language("bin/run", "#!/usr/bin/env python3\n", config).language)
        self.assertEqual("json", detect_language("config", '{ "a": 1 }\n', config).language)

    def test_detect_language_uses_overrides(self) -> None:
        config = ReviewConfig(extension_overrides={".tpl": "html"})
        self.assertEqual("html", detect_language("views/index.tpl", "", config).language)


if __name__ == "__main__":
    unittest.main()
