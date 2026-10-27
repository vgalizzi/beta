"""Logging setup tests: the application's loggers are visible, third-party ones are not."""

import io
import logging
from collections.abc import Iterator

import pytest

from beta.core.logging_config import APP_LOGGER_NAME, HANDLER_NAME, configure_logging


@pytest.fixture(autouse=True)
def pristine_app_logger() -> Iterator[logging.Logger]:
    """Give each test an unconfigured "beta" logger, and restore it afterwards.

    Loggers are process-wide singletons: without this, a handler added by one test
    would still be there in the next one.
    """
    logger = logging.getLogger(APP_LOGGER_NAME)
    saved = (logger.level, logger.propagate, logger.handlers[:])
    logger.handlers.clear()
    yield logger
    logger.setLevel(saved[0])
    logger.propagate = saved[1]
    logger.handlers[:] = saved[2]


def _own_handlers(logger: logging.Logger) -> list[logging.Handler]:
    # pytest attaches capture handlers of its own to loggers that do not propagate.
    return [handler for handler in logger.handlers if handler.get_name() == HANDLER_NAME]


def test_info_from_an_application_module_is_written(pristine_app_logger: logging.Logger) -> None:
    configure_logging("INFO")
    stream = io.StringIO()
    (handler,) = _own_handlers(pristine_app_logger)
    assert isinstance(handler, logging.StreamHandler)
    handler.setStream(stream)

    # Module loggers are named after the module, hence children of "beta".
    logging.getLogger("beta.api.app").info("Settings loaded")

    assert "INFO beta.api.app: Settings loaded" in stream.getvalue()


def test_records_below_the_configured_level_are_dropped() -> None:
    configure_logging("WARNING")

    assert not logging.getLogger("beta.api.app").isEnabledFor(logging.INFO)


def test_configuring_twice_does_not_duplicate_lines(pristine_app_logger: logging.Logger) -> None:
    # The lifespan calls it at every application start, and the tests start the app often.
    configure_logging("INFO")
    configure_logging("INFO")

    assert len(_own_handlers(pristine_app_logger)) == 1


def test_third_party_loggers_stay_quiet() -> None:
    # The reason the root logger is left alone: at INFO, SQLAlchemy logs every statement.
    configure_logging("DEBUG")

    assert not logging.getLogger("sqlalchemy.engine").isEnabledFor(logging.INFO)
    assert not logging.getLogger(APP_LOGGER_NAME).propagate
