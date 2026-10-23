from typing import Literal

from pydantic import BaseModel, computed_field

# State of a single dependency, and of the API as a whole (ADR 0004).
DependencyState = Literal["ok", "down"]


class HealthStatus(BaseModel):
    database: DependencyState
    broker: DependencyState
    storage: DependencyState

    # mypy rejects any decorator stacked on top of @property ("prop-decorator").
    # Pydantic documents this exact stacking for computed fields, so the error is a
    # false positive here.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def status(self) -> DependencyState:
        dependencies = (self.database, self.broker, self.storage)
        return "ok" if all(dep == "ok" for dep in dependencies) else "down"
