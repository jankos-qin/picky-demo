from __future__ import annotations

import logging
import os


LOGGER_NAME = "ai_pr_review"


def configure_logging() -> logging.Logger:
    level_name = os.environ.get("PICKY_LOG_LEVEL", "INFO").strip().upper() or "INFO"
    level = getattr(logging, level_name, logging.INFO)

    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [picky] %(message)s")
        )
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    base = configure_logging()
    if not name:
        return base
    return base.getChild(name)
