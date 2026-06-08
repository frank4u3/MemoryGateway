import logging
import sys

from pythonjsonlogger import jsonlogger

from .config import settings


def setup_logging() -> None:
    logger = logging.getLogger("gateway")
    logger.setLevel(settings.log_level)

    handler = logging.StreamHandler(sys.stdout)
    formatter = jsonlogger.JsonFormatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s",
        rename_fields={"asctime": "timestamp"},
    )
    handler.setFormatter(formatter)

    logger.handlers.clear()
    logger.addHandler(handler)
    logger.propagate = False


def get_logger() -> logging.Logger:
    return logging.getLogger("gateway")
