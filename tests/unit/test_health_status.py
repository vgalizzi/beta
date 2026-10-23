"""HealthStatus tests: the overall status is derived from the three dependencies."""

import pytest

from beta.api.schemas import HealthStatus


def test_status_is_ok_when_every_dependency_is_ok() -> None:
    health = HealthStatus(database="ok", broker="ok", storage="ok")

    assert health.status == "ok"


@pytest.mark.parametrize("failing", ["database", "broker", "storage"])
def test_status_is_down_when_any_dependency_is_down(failing: str) -> None:
    states = {"database": "ok", "broker": "ok", "storage": "ok"} | {failing: "down"}

    health = HealthStatus.model_validate(states)

    assert health.status == "down"


def test_status_is_serialized_with_the_dependencies() -> None:
    # A computed field is not an input, but it must appear in the response body.
    health = HealthStatus(database="ok", broker="down", storage="ok")

    assert health.model_dump() == {
        "database": "ok",
        "broker": "down",
        "storage": "ok",
        "status": "down",
    }
