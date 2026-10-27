import logging
import sys

# Only the application's own logger tree is configured, not the root logger. A root logger
# at INFO would also enable third-party loggers: SQLAlchemy would log every statement,
# including the health check's SELECT 1 on each probe. uvicorn configures its own loggers.
APP_LOGGER_NAME = "beta"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
HANDLER_NAME = "beta-stderr"


def configure_logging(level: str) -> None:
    logger = logging.getLogger(APP_LOGGER_NAME)
    logger.setLevel(level)
    # Records stop here: a handler added to the root logger later would print them twice.
    logger.propagate = False
    # Idempotent: the lifespan runs at every application start, several times in tests.
    # The handler is found by name, because other tools attach handlers of their own
    # (pytest adds its capture handlers to loggers that do not propagate).
    if not any(handler.get_name() == HANDLER_NAME for handler in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.set_name(HANDLER_NAME)
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        logger.addHandler(handler)
